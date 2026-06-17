from __future__ import annotations

import csv
from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orcheo.graph.ingestion.summary import (
    _as_state_graph,
    _serialise_branch,
    _unwrap_runnable,
)
from orcheo.graph.state import State
from orcheo.nodes.logic import FinalReplyNode, StructuredRouterDispatchNode
from orcheo.nodes.qualitative import codebook as codebook_module
from orcheo.nodes.qualitative.codebook import (
    Codebook,
    code_to_theme_map,
    Subtheme,
    Theme,
    fallback_codebook,
    merge_codebooks,
    recover_exportable_codebook,
    parse_codebook_csv,
    parse_codebook_markdown,
    parse_markdown_table_row,
    render_codebook_for_prompt,
)
from orcheo.nodes.qualitative.coded_data import (
    build_coded_data_csv,
    parse_coded_data_csv,
)
from orcheo.nodes.qualitative.coding import with_inferred_sentiment
from orcheo.nodes.qualitative.insights import (
    CandidateInsight,
    ReportData,
    fallback_insights,
    fallback_quotes,
    filter_grounded_quotes,
    normalise_candidate_insights,
    recommend_action,
    recommend_impact,
    critique_insights,
)
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CodeAssignment,
    CodeAssignmentEntry,
    CooccurrenceRow,
    QuantificationRow,
    Quote,
    Recommendation,
    SegmentBreakdownRow,
    SegmentComparison,
    SegmentVariable,
    Unit,
    OpenCodingBatchResponse,
    RecodingBatchResponse,
)
from orcheo.nodes.qualitative.pipeline import (
    ContextPreNode,
    CodebookOutputNode,
    ExportCodebookNode,
    ExportCodedDataNode,
    FileValidatorNode,
    IngestNode,
    RecodeOutputNode,
    SetupNode,
)
from orcheo.nodes.qualitative.quantify import CodedDataIngestNode
from orcheo.nodes.qualitative.quality import assess_quality
from orcheo.nodes.qualitative.quantify import (
    compare_segments,
    compute_quantification,
    compute_segment_breakdowns,
    parse_str_list,
    plan_segments,
)
from orcheo.nodes.qualitative.report import (
    ExportReportNode,
    ReportOutputNode,
    render_markdown_report,
    validate_final_state,
)
from orcheo.nodes.qualitative.sources import SourceParser
from orcheo.nodes.qualitative.stages import (
    LLMStageFinalizeNode,
    LLMStagePrepareNode,
)
from orcheo.runtime.results import assistant_message_texts

from tests.nodes.test_qualitative_helpers import (
    _assignments,
    _codebook,
    _report_state,
    _units,
)


def test_runtime_results_cover_empty_content_and_non_messages() -> None:
    state = State(
        {
            "messages": [
                {"role": "assistant", "content": " "},
                {"type": "assistant", "content": "kept"},
                AIMessage(content=" "),
                AIMessage(content="again"),
                "ignore-me",
            ]
        }
    )

    assert assistant_message_texts(state) == ["again", "kept"]


@pytest.mark.asyncio()
async def test_routing_nodes_cover_message_branch() -> None:
    router = StructuredRouterDispatchNode(name="router")
    result = await router(
        State(
            {
                "structured_response": {
                    "action": "respond",
                    "branch": "ignored",
                    "message": "hello",
                }
            }
        ),
        {},
    )

    assert result["assistant_message"] == "hello"
    assert result["results"]["router"]["routing"] == "respond"


def test_summary_helpers_cover_false_paths() -> None:
    class DummyModel(BaseModel):
        value: int = 1

    class WithFunc:
        func = DummyModel()

    class Branch:
        ends = {"yes": "__end__", "no": "next"}
        then = "__start__"

        class Path:
            func = lambda: None  # noqa: E731

        path = Path()

    class EmptyBranch:
        pass

    assert _unwrap_runnable(WithFunc()) is WithFunc.func
    fake_compiled = object.__new__(CompiledStateGraph)
    object.__setattr__(fake_compiled, "builder", object())
    assert _as_state_graph(fake_compiled) is None
    payload = _serialise_branch("source", "branch", Branch())
    assert payload["mapping"] == {"yes": "END", "no": "next"}
    assert payload["default"] == "START"
    assert payload["callable"] == "<lambda>"
    assert _serialise_branch("source", "branch", EmptyBranch()) == {
        "source": "source",
        "branch": "branch",
    }

    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    assert _as_state_graph(graph.compile()) is graph


def test_codebook_helpers_cover_edge_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = fallback_codebook(
        [
            CodeAssignment(
                unit_id="U1",
                assignments=[
                    CodeAssignmentEntry(code_id="", evidence="skip"),
                    CodeAssignmentEntry(code_id="alpha_beta", evidence="Alpha"),
                    CodeAssignmentEntry(code_id="alpha_beta", evidence="Alpha"),
                ],
            )
        ]
    )
    assert fallback.themes[0].subthemes[0].example_quotes[0]["text"] == "Alpha"

    assert parse_markdown_table_row("   ") is None
    assert parse_markdown_table_row(r"| a \| b | c |") == ["a | b", "c"]

    csv_text = (
        "theme_id,theme_title,code_id,code_title,definition,include,exclude\n"
        "T1,Theme,,Missing,Desc,,\n"
        "T1,Theme,C1,Alpha,Desc,keep; also,drop\n"
    )
    parsed = parse_codebook_csv(csv_text)
    assert parsed is not None
    assert parsed.themes[0].subthemes[0].code_id == "C1"

    assert (
        parse_codebook_csv(
            "theme_id,theme_title,unit_id,code_id\nT1,Theme,1,C1\n",
            reject_coded_data=True,
        )
        is None
    )

    monkeypatch.setattr(
        codebook_module.csv,
        "DictReader",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("boom")),
    )
    assert parse_codebook_csv("theme_id,theme_title,code_id\nT1,Theme,C1\n") is None
    monkeypatch.undo()

    assert (
        parse_codebook_csv(
            "theme_id,theme_title,code_id,code_title,definition,include,exclude\n"
            "T1,Theme,,Missing,Desc,,\n"
        )
        is None
    )

    markdown = parse_codebook_markdown(
        """
| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Theme | C1 | Alpha | Desc | keep | drop |
| T1 | Theme |  | Missing | Desc | keep | drop |
| T1 | Theme | C3 |
"""
    )
    assert markdown is not None
    assert [theme.theme_id for theme in markdown.themes] == ["T1"]

    headings = parse_codebook_markdown("## T2: Heading\n\n- `C2` **Beta**: Beta def\n")
    assert headings is not None
    assert [theme.theme_id for theme in headings.themes] == ["T2"]


def test_coded_data_helpers_cover_branch_paths() -> None:
    codebook = _codebook()
    units = _units()
    csv_text = (
        "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
        "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,"
        "confidence,sentiment\n"
        ",R0,s,,,,,,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n"
        "U1,R1,s,,,,[],,1,C1,T1,Theme,Code,Def,Ev,0.5,unexpected\n"
        "U2,R2,s,,,,{bad},flag; ,1,,T1,Theme,Code,Def,Ev,0.5,neutral\n"
        'U3,R3,s,,,,{"x":1},,1,C2,T2,Other,Code,Def,Ev,0.5,neutral\n'
    )
    parsed = parse_coded_data_csv(csv_text)
    assert parsed is not None
    parsed_units, parsed_assignments, parsed_codebook = parsed
    assert [unit.unit_id for unit in parsed_units] == ["U1", "U2", "U3"]
    assert parsed_assignments[0].assignments[0].sentiment == "neutral"
    assert parsed_codebook is not None

    assert parse_coded_data_csv("unit_id,text\nU1,hello\n") is None
    assert (
        parse_coded_data_csv(
            "unit_id,record_id,source,text,assignment_index,code_id\n,1,s,hello,1,C1\n"
        )
        is None
    )


def test_coding_helpers_cover_sentiment_variants() -> None:
    neutral = CodeAssignmentEntry(code_id="C1", evidence="plain", sentiment="neutral")
    assert (
        with_inferred_sentiment(
            neutral,
            Unit(
                unit_id="U1",
                record_id="R1",
                source="s",
                text="easy helpful but hard and confusing",
                original_text="easy helpful but hard and confusing",
            ),
        ).sentiment
        == "mixed"
    )
    assert (
        with_inferred_sentiment(
            neutral.model_copy(update={"evidence": "hard"}),
            Unit(
                unit_id="U2",
                record_id="R2",
                source="s",
                text="hard and confusing",
                original_text="hard and confusing",
            ),
        ).sentiment
        == "negative"
    )
    assert (
        with_inferred_sentiment(
            neutral.model_copy(update={"evidence": "clear"}),
            Unit(
                unit_id="U3",
                record_id="R3",
                source="s",
                text="easy and helpful",
                original_text="easy and helpful",
            ),
        ).sentiment
        == "positive"
    )
    assert (
        with_inferred_sentiment(
            neutral,
            Unit(
                unit_id="U4",
                record_id="R4",
                source="s",
                text="plain text",
                original_text="plain text",
            ),
        ).sentiment
        == "neutral"
    )
    assert (
        with_inferred_sentiment(
            neutral.model_copy(update={"sentiment": "mixed"}), None
        ).sentiment
        == "mixed"
    )


def test_quality_helpers_cover_ai_like_and_report_exclusion() -> None:
    units = [
        Unit(unit_id="U1", record_id="R1", source="s", text="", original_text=""),
        Unit(
            unit_id="U2", record_id="R2", source="s", text="same", original_text="same"
        ),
        Unit(
            unit_id="U3", record_id="R3", source="s", text="same", original_text="same"
        ),
        Unit(
            unit_id="U4",
            record_id="R4",
            source="s",
            text="As an AI, I agree",
            original_text="As an AI, I agree",
        ),
    ]
    updated, report = assess_quality(units)
    assert "ai_like" in updated[3].quality_flags
    assert "duplicate" in updated[2].quality_flags
    assert report.excluded_units >= 1


def test_quantify_helpers_cover_branching() -> None:
    assert parse_str_list(["a", "", "b"]) == ["a", "b"]
    assert parse_str_list("a, b") == ["a", "b"]
    assert parse_str_list(None) == []

    codebook = _codebook()
    units = _units() + [
        Unit(
            unit_id="U3",
            record_id="R2",
            source="s",
            text="extra",
            original_text="extra",
            metadata={"segment": None, "plan": "pro"},
        )
    ]
    assignments = _assignments() + [
        CodeAssignment(
            unit_id="missing", assignments=[CodeAssignmentEntry(code_id="C1")]
        ),
        CodeAssignment(
            unit_id="U3", assignments=[CodeAssignmentEntry(code_id="missing")]
        ),
    ]
    rows, cooccurrence = compute_quantification(units, assignments, codebook)
    assert rows
    assert cooccurrence
    assert (
        compute_quantification(
            [
                Unit(
                    unit_id="U9",
                    record_id="R9",
                    source="s",
                    text="x",
                    original_text="x",
                )
            ],
            [
                CodeAssignment(
                    unit_id="U9", assignments=[CodeAssignmentEntry(code_id="missing")]
                )
            ],
            codebook,
        )[1]
        == []
    )

    variables = plan_segments(
        units
        + [
            Unit(
                unit_id="U4",
                record_id="R4",
                source="s",
                text="more",
                original_text="more",
                metadata={"plan": "", "single": "x"},
            )
        ],
        overrides=["plan", "missing"],
        max_values=5,
    )
    assert variables[0].name == "plan"
    breakdowns = compute_segment_breakdowns([], units, assignments, codebook)
    assert breakdowns == []
    breakdowns = compute_segment_breakdowns(
        [SegmentVariable(name="plan", values=["pro"], source="override")],
        units,
        assignments,
        codebook,
        min_sample_size=3,
    )
    assert breakdowns[0].sample_size_guard == "small_n"

    comparisons = compare_segments(
        [
            SegmentBreakdownRow(
                segment="plan",
                value="a",
                theme_id="T1",
                respondents=1,
                total_respondents=1,
                pct_respondents=50.0,
                sample_size_guard="ok",
            ),
            SegmentBreakdownRow(
                segment="plan",
                value="b",
                theme_id="T1",
                respondents=1,
                total_respondents=1,
                pct_respondents=50.0,
                sample_size_guard="ok",
            ),
        ]
    )
    assert comparisons == []


def test_report_helpers_cover_validation_and_rendering_branches() -> None:
    data = _report_state()
    assert validate_final_state(data) == []

    invalid = ReportData(
        approved_codebook=_codebook(),
        units=_units(),
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U1",
                assignments=[CodeAssignmentEntry(code_id="missing", confidence=1.0)],
            )
        ],
        quantification=[
            QuantificationRow(
                theme_id="missing",
                title="x",
                mentions=1,
                respondents=1,
                pct_respondents=100.0,
            )
        ],
        selected_quotes=[Quote(theme_id="missing", unit_id="missing", text="")],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="obs",
                supporting_codes=[],
                supporting_units=[],
            )
        ],
        approved_insight_ids=["I1", "missing"],
    )
    assert validate_final_state(invalid)
    rendered = render_markdown_report(data)
    assert "## Evidence Index" in rendered


def test_source_parser_covers_fallback_and_support_types() -> None:
    assert (
        SourceParser.sniff_type("support_ticket.csv", "a,b\n1,2") == "support_tickets"
    )
    assert SourceParser.sniff_type("chat.json", "[{}]") == "chat_log"
    assert SourceParser.sniff_type(None, "x,y\n1,2") == "survey_csv"

    rows = SourceParser.parse_survey_csv("id,text\n1,hello\n", flexible_columns=False)
    assert rows[0].record_id == "1"

    assert SourceParser.parse_transcript_json("not-json") == []
    assert SourceParser.parse_transcript("hello\nspeaker: world")  # plain path
    assert SourceParser.parse_chat_log(
        "not-json"
    ) == SourceParser.parse_transcript_plain("not-json")

    support = SourceParser.parse_support_tickets(
        '{"tickets": [{"id": "T1", "text": "Help", "requester": "A"}]}'
    )
    assert support[0].record_id == "T1"
    assert SourceParser.normalise_payload(State({}), None) is None


def test_codebook_helpers_cover_remaining_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Seed",
                subthemes=[Subtheme(code_id="C001", title="Keep", definition="Seed")],
            )
        ]
    )
    emergent = Codebook(
        themes=[
            Theme(
                theme_id="",
                title="Duplicate",
                subthemes=[Subtheme(code_id="", title="Keep", definition="Dup")],
            ),
            Theme(
                theme_id="",
                title="Fresh",
                subthemes=[
                    Subtheme(code_id="C001", title="Keep me", definition="Dup id"),
                    Subtheme(code_id="", title="New Title", definition="New"),
                ],
            ),
        ]
    )
    merged = merge_codebooks(seed, emergent)
    assert [theme.title for theme in merged.themes] == ["Seed", "Fresh"]
    assert [sub.code_id for sub in merged.themes[-1].subthemes] == ["C002", "C003"]

    assert parse_codebook_csv(
        "theme_id,theme_title,code_id,code_title,definition,include,exclude\n"
        "T1,Theme,C1,Alpha,Desc,one; two,drop\n"
    )
    assert parse_codebook_csv("theme_id,theme_title\nT1,Theme\n") is None
    assert (
        parse_codebook_csv(
            "theme_id,theme_title,unit_id,code_id\nT1,Theme,1,C1\n",
            reject_coded_data=True,
        )
        is None
    )

    parsed = parse_codebook_markdown(
        """
| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Theme &amp; One | C1 | Alpha | Desc | keep | drop |
| T1 | Theme &amp; One |  | Skip | Desc | keep | drop |
| T2 | Theme Two | C2 | Beta | Desc | stay | leave |
| T2 | Theme Two |
"""
    )
    assert parsed is not None
    assert [theme.theme_id for theme in parsed.themes] == ["T1", "T2"]
    assert parsed.themes[0].subthemes[0].title == "Alpha"

    headings = parse_codebook_markdown("## T3: Heading\n\n- `C4` **Beta**: Body\n")
    assert headings is not None
    assert headings.themes[0].subthemes[0].code_id == "C4"
    assert parse_codebook_markdown("| a | b |\n| - | - |\n") is None

    state = State(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "## T5: Recovered\n- `C9` **Beta**: Body\n",
                }
            ]
        }
    )
    recovered = recover_exportable_codebook(state)
    assert recovered is not None

    assert code_to_theme_map(merged)["C001"] == ("T1", "Seed")
    assert render_codebook_for_prompt(merged).startswith("T1: Seed")
    monkeypatch.setattr(
        codebook_module.csv,
        "DictReader",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("boom")),
    )
    assert parse_codebook_csv("theme_id,theme_title,code_id\nT1,Theme,C1\n") is None


def test_codebook_and_coded_data_helpers_cover_parsing_edges() -> None:
    invalid_csv = (
        "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
        "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,"
        "confidence,sentiment\n"
        "U1,R1,s,,text,text,{bad},flag; ,1,C1,T1,Theme,Code,Def,Ev,not-a-number,weird\n"
    )
    parsed = parse_coded_data_csv(invalid_csv)
    assert parsed is not None
    units, assignments, codebook = parsed
    assert units[0].metadata == {}
    assert assignments[0].assignments[0].confidence == 0.0
    assert assignments[0].assignments[0].sentiment == "neutral"
    assert codebook is not None

    assert parse_coded_data_csv("unit_id,text\nU1,plain\n") is None
    assert (
        parse_coded_data_csv(
            "unit_id,record_id,source,text,assignment_index,code_id\n,1,s,hello,1,C1\n"
        )
        is None
    )


def test_insights_helpers_cover_branching() -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    quotes = fallback_quotes(codebook, assignments, units, quotes_per_theme=1)
    assert quotes
    assert (
        filter_grounded_quotes(
            quotes + [Quote(theme_id="bad", unit_id="bad", text="")], codebook, units
        )
        == quotes
    )
    assert [
        insight.insight_id
        for insight in normalise_candidate_insights(
            [CandidateInsight(insight_id="", observation="obs")]
        )
    ][0].startswith("I")

    report_data = _report_state()
    assert fallback_insights(report_data)
    assert critique_insights(report_data)
    assert recommend_action(CandidateInsight(insight_id="I1", observation="obs")) == (
        "Validate this pattern with follow-up research before committing roadmap work."
    )
    assert recommend_impact(
        CandidateInsight(insight_id="I1", observation="obs", evidence_strength="medium")
    ).startswith("May reduce friction")


def test_insights_helpers_cover_remaining_branches() -> None:
    codebook = _codebook()
    units = _units()
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[
                CodeAssignmentEntry(code_id="C1", evidence="clear", confidence=0.9),
                CodeAssignmentEntry(code_id="missing", evidence="skip", confidence=0.1),
            ],
        ),
        CodeAssignment(
            unit_id="U0002",
            assignments=[
                CodeAssignmentEntry(
                    code_id="C1",
                    evidence="but confusing",
                    confidence=0.7,
                    sentiment="negative",
                )
            ],
        ),
        CodeAssignment(
            unit_id="MISSING",
            assignments=[CodeAssignmentEntry(code_id="C1", evidence="ghost")],
        ),
    ]
    quotes = fallback_quotes(codebook, assignments, units, quotes_per_theme=1)
    assert len(quotes) == 1
    assert quotes[0].unit_id == "U0001"
    assert filter_grounded_quotes(
        [
            Quote(theme_id="T1", unit_id="U0001", text="valid"),
            Quote(theme_id="bad", unit_id="U0001", text="valid"),
            Quote(theme_id="T1", unit_id="bad", text="valid"),
            Quote(theme_id="T1", unit_id="U0001", text=" "),
        ],
        codebook,
        units,
    ) == [Quote(theme_id="T1", unit_id="U0001", text="valid")]
    assert (
        normalise_candidate_insights(
            [
                CandidateInsight(insight_id=" I99 ", observation="obs"),
                CandidateInsight(insight_id="", observation="obs"),
            ]
        )[0].insight_id
        == "I99"
    )
    assert fallback_insights(ReportData(approved_codebook=None)) == []

    report_data = ReportData(
        approved_codebook=codebook,
        units=units,
        code_assignments_pass2=assignments,
        quantification=[
            QuantificationRow(
                theme_id="T9",
                title="Missing",
                mentions=4,
                respondents=4,
                pct_respondents=80.0,
            ),
            QuantificationRow(
                theme_id="T1",
                title="Onboarding",
                mentions=3,
                respondents=1,
                pct_respondents=50.0,
            ),
            QuantificationRow(
                theme_id="T2",
                title="Support",
                mentions=2,
                respondents=2,
                pct_respondents=25.0,
            ),
            QuantificationRow(
                theme_id="T1",
                title="Onboarding",
                mentions=1,
                respondents=0,
                pct_respondents=0.0,
            ),
        ],
        segment_comparisons=[
            SegmentComparison(
                segment="plan",
                theme_id="T1",
                high_value="pro",
                low_value="basic",
                high_pct=90.0,
                low_pct=10.0,
                delta_pct=80.0,
                signal="weak",
                note="Weak split",
            )
        ],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding is clear",
                interpretation="Users can get started quickly.",
                implication="Keep the flow simple.",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="high",
            ),
            CandidateInsight(
                insight_id="I2",
                observation="Support is notable",
                interpretation="Support matters.",
                implication="Investigate.",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="medium",
            ),
        ],
    )
    fallback = fallback_insights(report_data)
    assert [insight.insight_id for insight in fallback] == ["I02", "I03"]
    critiqued = critique_insights(report_data)
    assert critiqued[0].evidence_strength == "medium"
    assert critiqued[1].evidence_strength == "low"
    assert any("Counter-evidence" in note for note in critiqued[0].critic_notes)
    assert any("Weak segment difference" in note for note in critiqued[0].critic_notes)
    assert recommend_action(
        CandidateInsight(
            insight_id="I3",
            observation="obs",
            counter_evidence_units=["U1"],
        )
    ).startswith("Investigate the counter-evidence")
    assert recommend_action(
        CandidateInsight(
            insight_id="I4",
            observation="obs",
            evidence_strength="high",
        )
    ).startswith("Prioritise a targeted experiment")
    assert recommend_impact(
        CandidateInsight(
            insight_id="I5",
            observation="obs",
            evidence_strength="high",
        )
    ).startswith("Likely to improve")
    assert recommend_impact(
        CandidateInsight(insight_id="I6", observation="obs", evidence_strength="low")
    ).startswith("Useful as a hypothesis")


def test_source_parser_covers_remaining_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert SourceParser.sniff_type("support.log", "a,b") == "support_tickets"
    assert SourceParser.sniff_type("conversation.json", "[]") == "chat_log"
    assert SourceParser.sniff_type("notes.txt", "hello") == "transcript"
    assert SourceParser.sniff_type(None, '{"messages": []}') == "chat_log"
    assert SourceParser.sniff_type(None, '{"subject": "x"}') == "support_tickets"
    assert SourceParser.sniff_type(None, "plain text") == "transcript"

    monkeypatch.setattr(
        csv.Sniffer,
        "sniff",
        lambda *args, **kwargs: (_ for _ in ()).throw(csv.Error("boom")),
    )
    rows = SourceParser.parse_survey_csv(
        "respondent_id,response,extra\n1,hello,x\n2,,y\n",
        flexible_columns=True,
    )
    assert len(rows) == 1
    assert rows[0].record_id == "1"
    assert rows[0].metadata == {"extra": "x"}
    assert SourceParser.parse_survey_csv("id,comment\n1,hello\n") == []

    transcript = SourceParser.parse_transcript_json(
        '[1, {"text": "", "speaker": "A"}, {"text": "Hello", "participant": "B", "x": 1}]'
    )
    assert len(transcript) == 1
    assert transcript[0].speaker == "B"
    assert transcript[0].metadata == {"x": 1}
    assert SourceParser.parse_transcript_plain(
        "speaker: hello\n\nno colon\nspeaker2:\n"
    )
    assert SourceParser.parse_transcript(
        "hello"
    ) == SourceParser.parse_transcript_plain("hello")
    chat = SourceParser.parse_chat_log(
        '{"id": "CHAT9", "turns": [{"text": "", "role": "ignored"}, {"text": "Hi", "role": "agent"}]}'
    )
    assert chat[0].source == "chat_log:agent"
    assert SourceParser.parse_chat_log(
        "not-json"
    ) == SourceParser.parse_transcript_plain("not-json")

    support_json = SourceParser.parse_support_tickets(
        '{"tickets": [{"description": "Help", "requester": "A", "id": "T1"}, {"message": "More", "requester": null}]}'
    )
    assert support_json[0].record_id == "T1"
    support_csv = SourceParser.parse_support_tickets(
        "id,answer,foo\n1,Need help,x\n", filename="tickets.csv", flexible_columns=True
    )
    assert support_csv[0].source == "support_ticket:tickets.csv"
    assert SourceParser.load_payload_content({"content": " hi "}) == " hi "
    assert SourceParser.load_payload_content({"storage_path": "/tmp/x"}) == ""
    assert SourceParser.parse_payload(None) == ([], "survey_csv")
    assert SourceParser.parse_payload({"content": "", "source_type": "survey_csv"}) == (
        [],
        "survey_csv",
    )
    parsed, source_type = SourceParser.parse_payload(
        {
            "content": '{"messages": []}',
            "source_type": "chat_log",
            "filename": "x.json",
        },
        allow_additional_sources=True,
    )
    assert source_type == "chat_log"
    assert parsed == []
    parsed, source_type = SourceParser.parse_payload(
        {"content": "hello", "source_type": "unsupported", "filename": "x.txt"}
    )
    assert source_type == "transcript"
    assert parsed

    state = State(
        {
            "inputs": {
                "documents": [
                    {
                        "content": "hello",
                        "storage_path": str((__file__)),
                        "name": "file.txt",
                        "source_type": "survey_csv",
                    }
                ]
            }
        }
    )
    assert SourceParser.normalise_payload(state, None)["filename"] == "file.txt"
    assert (
        SourceParser.normalise_payload(
            State({}),
            {
                "configurable": {
                    "source": "id,text\n1,hello\n",
                    "source_filename": "x.csv",
                }
            },
        )["filename"]
        == "x.csv"
    )


@pytest.mark.asyncio()
async def test_qualitative_remaining_branch_gaps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    keys = QualitativeResultKeys()

    # Codebook
    assert parse_codebook_csv(
        "theme_id,theme_title,code_id,code_title,definition,include,exclude\n"
        "T1,Theme,C1,Alpha,Desc,one; two,drop\n"
        "T1,Theme,C2,Beta,Desc,keep,leave\n"
    )
    assert (
        parse_codebook_markdown(
            "| Theme ID | Theme Title | Code ID |\n| --- | --- | --- |\n| T1 | Theme | C1 |\n"
        )
        is None
    )
    assert recover_exportable_codebook(State({"messages": []})) is None

    # Coded data
    no_assignment_csv, total = build_coded_data_csv(
        [units[0]],
        [],
        codebook,
    )
    assert total == 0
    assert "U0001" in no_assignment_csv
    duplicate_csv = (
        "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
        "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,"
        "confidence,sentiment\n"
        "U1,R1,s,,hello,hello,{},,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n"
        "U2,R2,s,,hello,hello,,,\n"
        'U3,R3,s,,hello,hello,{"x":1},,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n'
    )
    parsed = parse_coded_data_csv(duplicate_csv)
    assert parsed is not None
    parsed_units, parsed_assignments, parsed_codebook = parsed
    assert len(parsed_units) == 3
    assert parsed_units[1].metadata == {}
    assert parsed_assignments[0].assignments[0].code_id == "C1"
    assert parsed_codebook is not None

    # Insights
    assert critique_insights(
        ReportData(
            candidate_insights=[CandidateInsight(insight_id="I1", observation="obs")],
            approved_codebook=None,
        )
    ) == [CandidateInsight(insight_id="I1", observation="obs")]
    low_report = ReportData(
        approved_codebook=codebook,
        units=units
        + [
            Unit(
                unit_id="U0003",
                record_id="R3",
                source="survey",
                text="clear",
                original_text="clear",
                metadata={"plan": "pro"},
            )
        ],
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U0003",
                assignments=[
                    CodeAssignmentEntry(
                        code_id="C1",
                        evidence="clear but confusing",
                        sentiment="negative",
                    )
                ],
            )
        ],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="high",
            ),
            CandidateInsight(
                insight_id="I2",
                observation="Support",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="medium",
            ),
            CandidateInsight(
                insight_id="I3",
                observation="Neutral",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="low",
            ),
        ],
        segment_comparisons=[
            SegmentComparison(
                segment="plan",
                theme_id="T1",
                high_value="pro",
                low_value="basic",
                high_pct=90.0,
                low_pct=10.0,
                delta_pct=80.0,
                signal="weak",
                note="Weak split",
            )
        ],
    )
    critiqued = critique_insights(low_report)
    assert critiqued[0].evidence_strength == "medium"
    assert critiqued[1].evidence_strength == "low"
    assert critiqued[2].evidence_strength == "low"

    # Quantify
    rows = compute_segment_breakdowns(
        [SegmentVariable(name="plan", source="override")],
        [
            Unit(
                unit_id="U9",
                record_id="R9",
                source="survey",
                text="hi",
                original_text="hi",
                metadata={"plan": ""},
            ),
            Unit(
                unit_id="U10",
                record_id="R10",
                source="survey",
                text="hi",
                original_text="hi",
                metadata={"plan": "pro"},
            ),
        ],
        [],
        codebook,
    )
    assert any(row.sample_size_guard == "small_n" for row in rows)

    # Sources
    assert (
        SourceParser.parse_payload(
            {
                "content": '{"tickets": [{"description": "Help", "requester": "A"}]}',
                "source_type": "support_tickets",
                "filename": "tickets.json",
            },
            allow_additional_sources=True,
        )[1]
        == "support_tickets"
    )
    assert (
        SourceParser.parse_chat_log(
            '{"conversations": [{"conversation_id": "C1", "messages": [{"text": "Hi", "role": "agent"}]}, 1]}'
        )[0].record_id
        == "C1:1"
    )
    assert SourceParser.parse_support_tickets("{bad}") == []
    assert (
        SourceParser.normalise_payload(
            State(
                {"inputs": {"documents": [{"storage_path": str(tmp_path / "x.txt")}]}}
            ),
            None,
        )
        is not None
    )

    # Pipeline
    class Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return Attachment("café".encode("latin-1"), "")

    file_path = tmp_path / "latin1.txt"
    file_path.write_bytes("café".encode("latin-1"))
    context_pre = ContextPreNode(name="context_pre")
    context_out = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [
                        {"attachment_id": "a1"},
                        {"storage_path": str(file_path)},
                    ]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert (
        "café"
        in context_out["results"]["context_pre"]["pending_documents"][0]["content"]
    )

    setup = SetupNode(
        name="setup",
        resolve_objective=True,
        resolve_codebook=True,
        resolve_seed_codebook=True,
        source_kind="coded_data",
    )
    setup_out = await setup(
        State(
            {
                "inputs": {"research_objective": "Identify friction"},
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {
                                "filename": "coded_data.csv",
                                "content": (
                                    "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
                                    "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,confidence,sentiment\n"
                                    "U1,R1,s,,hello,hello,{},,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n"
                                ),
                            }
                        ]
                    }
                },
            }
        ),
        {},
    )
    assert setup_out["results"]["setup"]["objective"]

    validator = FileValidatorNode(
        name="validate_files",
        data_file_kind="raw",
        single_data_file=True,
        require_codebook=True,
        codebook_result_field="approved_codebook",
        coded_data_result_field="coded_payload",
        seed_codebook_result_field="seed_codebook_from_file",
        announce_seed_codebook=False,
    )

    def _raw_payload(payload, **kwargs):
        if payload.get("filename") in {"data1.csv", "data2.csv"}:
            return ([object()], "survey_csv")
        if payload.get("filename") == "coded.csv":
            return ([], "survey_csv")
        return ([], "unsupported")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload", _raw_payload
    )
    validation_state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"filename": "data1.csv", "content": "id,text\n1,Hello\n"},
                        {"filename": "data2.csv", "content": "id,text\n2,Hi\n"},
                        {"filename": "coded.csv", "content": duplicate_csv},
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Alpha,Desc\n"
                            ),
                        },
                        {"filename": "bad.txt", "content": "oops"},
                    ]
                }
            }
        }
    )
    validation_out = await validator(validation_state, {})
    assert "Multiple data files" in validation_out["assistant_message"]
    assert (
        "could not parse as a data file or codebook"
        in validation_out["assistant_message"]
    )

    codebook_output = CodebookOutputNode(
        name="codebook_output", max_coding_batches=1, default_batch_size=1
    )
    note_out = await codebook_output(
        State(
            {
                "results": {
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units],
                    },
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "per-run limit" in note_out["assistant_message"]

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/file.csv")

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("offline")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    export_codebook = ExportCodebookNode(name="export_codebook")
    export_codebook_out = await export_codebook(
        State(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "## T1: Theme\n- `C1` **Alpha**: Desc\n",
                    }
                ]
            }
        ),
        {},
    )
    assert "Download codebook.csv" in export_codebook_out["assistant_message"]

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    export_codebook_error = await export_codebook(
        State(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "## T1: Theme\n- `C1` **Alpha**: Desc\n",
                    }
                ]
            }
        ),
        {},
    )
    assert "Export failed" in export_codebook_error["assistant_message"]

    recode = RecodeOutputNode(name="recode_data")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    recode_out = await recode(
        State(
            {
                "results": {
                    "ingest": {"halt": False},
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "data_quality": {
                        "quality_report": {
                            "total_units": 2,
                            "flagged_units": 1,
                            "excluded_units": 0,
                        }
                    },
                    "recoder_finalize": {"total_batches": 2, "batch_end_index": 1},
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "only the first" in recode_out["assistant_message"]

    export_coded = ExportCodedDataNode(name="export_coded_data")
    export_coded_out = await export_coded(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {},
    )
    assert "Download coded_data.csv" in export_coded_out["assistant_message"]

    # Coded-data ingest and report nodes
    coded_ingest = CodedDataIngestNode(name="coded_ingest", allow_chained_results=True)
    ingest_missing = await coded_ingest(State({"results": {}}), {})
    assert ingest_missing["results"]["coded_ingest"]["halt"] is True

    chained = await coded_ingest(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {},
    )
    assert chained["results"]["coded_ingest"]["unit_count"] == 2

    report_data = ReportData(
        research_objective="Understand onboarding",
        approved_codebook=codebook,
        units=units,
        code_assignments_pass2=assignments,
        quantification=[
            QuantificationRow(
                theme_id="T1",
                title="Onboarding",
                mentions=2,
                respondents=2,
                pct_respondents=100.0,
            ),
        ],
        cooccurrence=[
            CooccurrenceRow(theme_id_a="T1", theme_id_b="T2", respondents=1, mentions=1)
        ],
        selected_quotes=[
            Quote(
                theme_id="T1",
                unit_id="U0001",
                text="The setup was clear.",
                speaker="Ana",
            )
        ],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding is clear",
                interpretation="Users can get started quickly.",
                implication="Keep the flow simple.",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="high",
                critic_notes=["Counter-evidence found in 1 unit(s): U0002."],
            ),
            CandidateInsight(
                insight_id="I2",
                observation="Support is notable",
                interpretation="Support matters.",
                implication="Investigate.",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
            ),
        ],
        recommendations=[
            Recommendation(
                insight_id="I1",
                finding="Onboarding is clear",
                action="Keep it.",
                expected_impact="Reduce friction.",
            ),
        ],
        approved_insight_ids=["I1", "I2"],
        segment_comparisons=[
            SegmentComparison(
                segment="plan",
                theme_id="T1",
                high_value="pro",
                low_value="basic",
                high_pct=90.0,
                low_pct=10.0,
                delta_pct=80.0,
                signal="weak",
                note="Weak split",
            )
        ],
    )
    assert validate_final_state(report_data) == []
    rendered = render_markdown_report(report_data)
    assert "Critic notes:" in rendered
    assert "## Recommendations" in rendered

    invalid = ReportData(
        approved_codebook=codebook,
        units=units,
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U1", assignments=[CodeAssignmentEntry(code_id="missing")]
            )
        ],
        quantification=[
            QuantificationRow(
                theme_id="missing",
                title="x",
                mentions=1,
                respondents=1,
                pct_respondents=100.0,
            )
        ],
        selected_quotes=[Quote(theme_id="missing", unit_id="missing", text="")],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="obs",
                supporting_codes=[],
                supporting_units=[],
            )
        ],
        approved_insight_ids=["I1", "missing"],
    )
    assert validate_final_state(invalid)

    report_output = ReportOutputNode(name="report_output")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.report.upload_attachment", _upload_fail
    )
    report_out = await report_output(
        State(
            {
                "results": {
                    "ingest": {"halt": False},
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "recommendation_generator": {
                        "candidate_insights": [
                            i.model_dump(mode="json")
                            for i in report_data.candidate_insights
                        ],
                        "recommendations": [
                            r.model_dump(mode="json")
                            for r in report_data.recommendations
                        ],
                        "approved_insight_ids": ["I1", "I2"],
                    },
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "data_quality": {
                        "quality_report": {
                            "total_units": 2,
                            "flagged_units": 1,
                            "excluded_units": 0,
                        }
                    },
                }
            }
        ),
        {},
    )
    assert "Could not generate the download link" in report_out["assistant_message"]

    export_report = ExportReportNode(name="export_report")
    monkeypatch.setattr("orcheo.nodes.qualitative.report.upload_attachment", _upload_ok)
    assert (
        "Download insight_report.md"
        in (
            await export_report(
                State(
                    {
                        "results": {
                            "setup": {
                                "approved_codebook": codebook.model_dump(mode="json")
                            },
                            "recommendation_generator": {
                                "candidate_insights": [
                                    i.model_dump(mode="json")
                                    for i in report_data.candidate_insights
                                ],
                                "recommendations": [
                                    r.model_dump(mode="json")
                                    for r in report_data.recommendations
                                ],
                            },
                        }
                    }
                ),
                {},
            )
        )["assistant_message"]
    )

    # Stage prepare/finalize dispatch branches
    stage_prepare = LLMStagePrepareNode(
        name="codebook_consolidator_prepare", stage="codebook_consolidator"
    )
    prepared = await stage_prepare(
        State(
            {
                "results": {
                    "setup": {keys.research_objective_field: "Objective"},
                    "open_coder_finalize": {
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {"configurable": {"seed_codebook": codebook.model_dump(mode="json")}},
    )
    assert prepared["results"]["codebook_consolidator_prepare"]["skip_llm"] is False

    stage_recoder = LLMStagePrepareNode(name="recoder_prepare", stage="recoder")
    recoder_prompt = await stage_recoder(
        State(
            {
                "results": {
                    "setup": {
                        keys.research_objective_field: "Objective",
                        keys.approved_codebook_field: codebook.model_dump(mode="json"),
                    },
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1, "per_turn_batch_budget": 1}},
    )
    assert recoder_prompt["results"]["recoder_prepare"]["skip_llm"] is False

    finalize_consolidator = LLMStageFinalizeNode(
        name="codebook_consolidator_finalize", stage="codebook_consolidator"
    )
    final_consolidator = await finalize_consolidator(
        State(
            {
                "results": {
                    "open_coder_finalize": {
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert (
        final_consolidator["results"]["codebook_consolidator_finalize"]["done"] is True
    )

    finalize_recoder = LLMStageFinalizeNode(
        name="recoder_finalize", stage="recoder", response_schema=RecodingBatchResponse
    )
    recoded = await finalize_recoder(
        State(
            {
                "structured_response": RecodingBatchResponse(
                    assignments=[
                        CodeAssignment(
                            unit_id="U0001",
                            assignments=[
                                CodeAssignmentEntry(
                                    code_id="C1", evidence="clear", confidence=0.9
                                )
                            ],
                        )
                    ]
                ).model_dump(mode="json"),
                "results": {
                    "setup": {
                        keys.approved_codebook_field: codebook.model_dump(mode="json")
                    },
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                    "recoder_finalize": {
                        "next_index": 0,
                        "batch_end_index": 1,
                        "total_batches": 1,
                        "batch_size": 1,
                    },
                },
            }
        ),
        {},
    )
    assert recoded["results"]["recoder_finalize"]["done"] in {True, False}

    finalize_quote = LLMStageFinalizeNode(
        name="quote_selector_finalize", stage="quote_selector"
    )
    quote_out = await finalize_quote(
        State(
            {
                "results": {
                    "setup": {
                        keys.approved_codebook_field: codebook.model_dump(mode="json")
                    },
                    "open_coder_finalize": {
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                }
            }
        ),
        {"configurable": {"quotes_per_theme": 1}},
    )
    assert quote_out["results"]["quote_selector_finalize"]["quotes"] >= 1

    finalize_insight = LLMStageFinalizeNode(
        name="insight_generator_finalize", stage="insight_generator"
    )
    insight_out = await finalize_insight(
        State(
            {
                "results": {
                    "setup": {
                        keys.approved_codebook_field: codebook.model_dump(mode="json")
                    },
                    "open_coder_finalize": {
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                }
            }
        ),
        {},
    )
    assert insight_out["results"]["insight_generator_finalize"]["halt"] is False


@pytest.mark.asyncio()
async def test_pipeline_nodes_cover_remaining_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    keys = QualitativeResultKeys()

    class _Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            raise RuntimeError("boom")

    source_file = tmp_path / "source.txt"
    source_file.write_text("hello", encoding="utf-8")
    context_pre = ContextPreNode(name="context_pre")
    context_result = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [
                        "skip-me",
                        {
                            "attachment_id": "att-1",
                            "storage_path": str(source_file),
                        },
                    ]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    pending = context_result["results"]["context_pre"]["pending_documents"]
    assert pending[0]["content"] == "hello"
    assert context_result["results"]["context_pre"]["source_hint"].startswith(
        "1 file(s)"
    )
    assert (await ContextPreNode(name="context_pre")(State({"results": {}}), {}))[
        "results"
    ]["context_pre"]["source_hint"] == "No files loaded yet."

    setup = SetupNode(
        name="setup",
        resolve_codebook=True,
        resolve_seed_codebook=True,
        exclude_codebook_docs=True,
    )
    setup_state = State(
        {
            "inputs": {},
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Onboarding,C1,Clear setup,Easy\n"
                            ),
                        },
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Hello there\n",
                        },
                    ]
                }
            },
        }
    )
    setup_result = (
        await setup(
            setup_state, {"configurable": {"research_objective": "  Objective  "}}
        )
    )["results"]["setup"]
    assert setup_result["objective"] == "Objective"
    assert setup_result["approved_codebook"]["themes"][0]["theme_id"] == "T1"
    assert setup_result["source_payload"]["filename"] == "survey.csv"
    assert setup_result["seed_codebook_from_file"]["themes"][0]["theme_id"] == "T1"
    assert setup_result["research_objective"] == "Objective"

    coded_setup = SetupNode(name="setup", source_kind="coded_data")
    coded_result = (
        await coded_setup(
            State(
                {
                    "results": {
                        "context_pre": {
                            "pending_documents": [
                                {
                                    "filename": "coded_data.csv",
                                    "content": (
                                        "unit_id,record_id,source,speaker,text,original_text,metadata,"
                                        "quality_flags,assignment_index,code_id,theme_id,theme_title,"
                                        "code_title,definition,evidence,confidence,sentiment\n"
                                        "U1,R1,s,,hello,hello,{},,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n"
                                    ),
                                }
                            ]
                        }
                    }
                }
            ),
            {},
        )
    )["results"]["setup"]
    assert coded_result["source_payload"]["filename"] == "coded_data.csv"

    ingest = IngestNode(name="ingest", require_codebook=True)
    halted = (await ingest(State({"results": {}}), {}))["results"]["ingest"]
    assert halted["halt"] is True
    no_records = (
        await IngestNode(name="ingest")(
            State(
                {"results": {"setup": {"source_payload": {"content": "id,text\n1,\n"}}}}
            ),
            {},
        )
    )["results"]["ingest"]
    assert no_records["halt"] is True

    validator = FileValidatorNode(
        name="validate_files",
        data_file_kind="auto",
        single_data_file=True,
        require_codebook=True,
        codebook_result_field="approved_codebook",
        coded_data_result_field="coded_payload",
        seed_codebook_result_field="seed_codebook_from_file",
        announce_seed_codebook=False,
        missing_data_message="Need data.",
    )
    classified = validator._classify(
        "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
        "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,"
        "confidence,sentiment\n"
        "U1,R1,s,,hello,hello,{},,1,C1,T1,Theme,Code,Def,Ev,0.5,neutral\n",
        "coded.csv",
    )
    assert classified[0] == "coded_data"
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda *args, **kwargs: ([], "unsupported"),
    )
    unknown = validator._classify("not parsable", "unknown.bin")
    assert unknown[0] == "unknown"
    validation_state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"filename": "data1.csv", "content": "id,text\n1,Hello\n"},
                        {"filename": "data2.csv", "content": "id,text\n2,Hi\n"},
                        {"filename": "coded.csv", "content": classified[1]["content"]},
                        {
                            "filename": "codebook1.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Onboarding,C1,Clear setup,Easy\n"
                            ),
                        },
                        {
                            "filename": "codebook2.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T2,Support,C2,Fast help,Fast\n"
                            ),
                        },
                        {
                            "filename": "broken.txt",
                            "content": "",
                            "load_error": "broken",
                        },
                    ]
                },
                "validate_files": {
                    "seed_codebook_from_file": codebook.model_dump(mode="json"),
                    "approved_codebook": codebook.model_dump(mode="json"),
                },
            }
        }
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: (
            [object(), object()]
            if payload.get("filename") in {"data1.csv", "data2.csv"}
            else [],
            "survey_csv",
        ),
    )
    validated = await validator(validation_state, {})
    message = validated["assistant_message"]
    assert "Multiple data files" in message
    assert "Multiple codebook CSV files" in message
    assert "broken" in message
    assert (
        validated["results"]["validate_files"]["coded_payload"]["filename"]
        == "coded.csv"
    )

    codebook_output = CodebookOutputNode(
        name="codebook_output",
        ingest_node_name="ingest",
        max_coding_batches=1,
        default_batch_size=1,
    )
    no_codebook = await codebook_output(
        State({"results": {"ingest": {"halt": False}}}), {}
    )
    assert no_codebook["assistant_message"] == codebook_output.no_codebook_message

    export_codebook = ExportCodebookNode(name="export_codebook")
    missing = await export_codebook(State({"messages": []}), {})
    assert missing["assistant_message"] == export_codebook.missing_codebook_message

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("offline")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    export_error = await export_codebook(
        State(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "## T1: Seed\n- `C1` **Keep**: Seed\n",
                    }
                ]
            }
        ),
        {},
    )
    assert "Export failed: offline" in export_error["assistant_message"]

    recode = RecodeOutputNode(name="recode_data")
    no_assignments = await recode(State({"results": {"ingest": {}}}), {})
    assert "No code assignments" in no_assignments["assistant_message"]

    report = {"total_units": 2, "flagged_units": 1, "excluded_units": 0}
    recode_state = State(
        {
            "results": {
                "ingest": {"halt": False},
                "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                "open_coder_finalize": {
                    "code_assignments_pass1": [
                        a.model_dump(mode="json") for a in assignments
                    ]
                },
                "data_quality": {"quality_report": report},
                "recoder_finalize": {"total_batches": 2, "batch_end_index": 1},
            }
        }
    )
    recode_error = await recode(recode_state, {"configurable": {"batch_size": 1}})
    assert "Could not generate the download link" in recode_error["assistant_message"]
    assert "Quality:" in recode_error["assistant_message"]
    assert "only the first 1 unit(s) were coded" in recode_error["assistant_message"]

    export_coded = ExportCodedDataNode(name="export_coded_data")
    missing_export = await export_coded(State({"results": {}}), {})
    assert missing_export["assistant_message"] == export_coded.missing_data_message


@pytest.mark.asyncio()
async def test_stage_nodes_cover_remaining_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    keys = QualitativeResultKeys()

    prepare = LLMStagePrepareNode(
        name="open_coder_prepare",
        stage="open_coder",
        max_coding_batches=1,
    )
    assert (await prepare(State({"results": {}}), {}))["results"]["open_coder_prepare"][
        "skip_llm"
    ]
    open_state = State(
        {
            "results": {
                "setup": {keys.research_objective_field: "Objective"},
                "ingest": {
                    keys.units_field: [u.model_dump(mode="json") for u in units]
                },
                "open_coder_finalize": {"next_index": 5},
            }
        }
    )
    assert (await prepare(open_state, {"configurable": {"batch_size": 1}}))["results"][
        "open_coder_prepare"
    ]["done"]

    consolidator = LLMStagePrepareNode(
        name="codebook_consolidator_prepare",
        stage="codebook_consolidator",
    )
    no_assignments = (await consolidator(State({"results": {}}), {}))["results"][
        "codebook_consolidator_prepare"
    ]
    assert no_assignments["action"] == "no_assignments"
    use_seed = (
        await consolidator(
            State(
                {
                    "results": {
                        "validate_files": {
                            keys.seed_codebook_field: codebook.model_dump(mode="json")
                        }
                    }
                }
            ),
            {"configurable": {"seed_codebook": codebook.model_dump(mode="json")}},
        )
    )["results"]["codebook_consolidator_prepare"]
    assert use_seed["action"] == "use_seed"

    recoder = LLMStagePrepareNode(
        name="recoder_prepare",
        stage="recoder",
        max_coding_batches=1,
    )
    assert (await recoder(State({"results": {}}), {}))["results"]["recoder_prepare"][
        "skip_llm"
    ]

    quote_selector = LLMStagePrepareNode(
        name="quote_selector_prepare", stage="quote_selector"
    )
    assert (await quote_selector(State({"results": {}}), {}))["results"][
        "quote_selector_prepare"
    ]["skip_llm"]
    quote_prompt = (
        await quote_selector(
            State(
                {
                    "results": {
                        "setup": {
                            keys.research_objective_field: "Objective",
                            keys.approved_codebook_field: codebook.model_dump(
                                mode="json"
                            ),
                        },
                        "open_coder_finalize": {
                            keys.assignments_field: [
                                a.model_dump(mode="json") for a in assignments
                            ],
                        },
                        "ingest": {
                            keys.units_field: [
                                u.model_dump(mode="json") for u in units
                            ],
                            keys.quantification_field: [],
                        },
                    }
                }
            ),
            {"configurable": {"quotes_per_theme": 1}},
        )
    )["results"]["quote_selector_prepare"]
    assert quote_prompt["fallback_quotes"]

    insight_generator = LLMStagePrepareNode(
        name="insight_generator_prepare",
        stage="insight_generator",
    )
    insight_prompt = (
        await insight_generator(
            State(
                {
                    "results": {
                        "setup": {
                            keys.research_objective_field: "Objective",
                            keys.approved_codebook_field: codebook.model_dump(
                                mode="json"
                            ),
                        },
                        "open_coder_finalize": {
                            keys.assignments_field: [
                                a.model_dump(mode="json") for a in assignments
                            ],
                        },
                        "ingest": {
                            keys.quantification_field: [],
                            keys.selected_quotes_field: [],
                        },
                    }
                }
            ),
            {},
        )
    )["results"]["insight_generator_prepare"]
    assert insight_prompt["fallback_insights"] == []

    finalize = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        response_schema=None,
    )
    assert (await finalize(State({"results": {"ingest": {}}}), {}))["results"][
        "open_coder_finalize"
    ]["done"] is True
    assert (
        await finalize(
            State(
                {
                    "results": {
                        "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                        "open_coder_finalize": {"next_index": 99},
                    }
                }
            ),
            {},
        )
    )["results"]["open_coder_finalize"]["done"]

    class Payload(BaseModel):
        value: int

    extractor = LLMStageFinalizeNode(name="extract", stage="open_coder")
    assert extractor._extract_llm_response(Payload(value=1), Payload) == Payload(
        value=1
    )
    assert extractor._extract_llm_response(
        {"structured_response": {"value": 2}},
        Payload,
    ) == Payload(value=2)
    assert (
        extractor._extract_llm_response(
            {"messages": [AIMessage(content="hello")]}, None
        )
        == "hello"
    )
    assert (
        extractor._extract_llm_response({"structured_response": "bad"}, Payload) is None
    )

    consolidator_finalize = LLMStageFinalizeNode(
        name="codebook_consolidator_finalize",
        stage="codebook_consolidator",
    )
    assert consolidator_finalize._finalize_consolidator(
        State({"results": {"open_coder_finalize": {}}}),
        {},
        {"action": "no_assignments"},
    ) == {"done": True}
    seed_result = consolidator_finalize._finalize_consolidator(
        State(
            {
                "results": {
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    }
                }
            }
        ),
        {"configurable": {"seed_codebook": codebook.model_dump(mode="json")}},
        {"action": "use_seed"},
    )
    assert "draft_codebook" in seed_result

    recoder_finalize = LLMStageFinalizeNode(
        name="recoder_finalize",
        stage="recoder",
        default_batch_size=1,
    )
    assert recoder_finalize._finalize_recoder(
        State({"results": {"ingest": {}, "setup": {}}}),
        {"skip_llm": True, "batch_index": 0},
    )["done"]
    assert recoder_finalize._finalize_recoder(
        State(
            {
                "results": {
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                }
            }
        ),
        {"batch_index": 99, "batch_end_index": 1, "total_batches": 1, "batch_size": 1},
    )["done"]

    quote_finalize = LLMStageFinalizeNode(
        name="quote_selector_finalize", stage="quote_selector"
    )
    assert (
        quote_finalize._finalize_quote_selector(
            State(
                {
                    "results": {
                        "setup": {
                            keys.approved_codebook_field: codebook.model_dump(
                                mode="json"
                            ),
                        },
                        "open_coder_finalize": {
                            keys.assignments_field: [
                                a.model_dump(mode="json") for a in assignments
                            ],
                        },
                        "ingest": {
                            keys.units_field: [
                                u.model_dump(mode="json") for u in units
                            ],
                        },
                    }
                }
            ),
            {"configurable": {"quotes_per_theme": 1}},
        )["quotes"]
        >= 1
    )

    insight_finalize = LLMStageFinalizeNode(
        name="insight_generator_finalize",
        stage="insight_generator",
    )
    insight_result = insight_finalize._finalize_insight_generator(
        State(
            {
                "results": {
                    "ingest": {
                        keys.approved_codebook_field: codebook.model_dump(mode="json"),
                        keys.units_field: [u.model_dump(mode="json") for u in units],
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ],
                        keys.selected_quotes_field: [],
                    }
                }
            }
        )
    )
    assert insight_result["halt"] is False

    unknown = LLMStageFinalizeNode.model_construct(name="unknown", stage="unknown")
    assert (await unknown(State({"results": {}}), {}))["results"]["unknown"][
        "halt"
    ] is True


@pytest.mark.asyncio()
async def test_qualitative_final_branch_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    keys = QualitativeResultKeys()

    # Codebook recovery and parsing fallthroughs.
    assert (
        parse_codebook_markdown(
            "| Theme ID | Theme Title | Code ID |\n| --- | --- | --- |\n| T1 | Theme | C1 |\n"
        )
        is None
    )
    assert parse_codebook_markdown("|\n|\n|\n") is None
    assert (
        parse_codebook_markdown(
            """
| A | B | C |
| --- | --- | --- |
| 1 | 2 | 3 |
"""
        )
        is None
    )
    repeated = parse_codebook_markdown(
        """
| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Theme | C1 | Alpha | Desc | keep | drop |
| T1 | Theme | C2 | Beta | Desc | keep | drop |
"""
    )
    assert repeated is not None and len(repeated.themes[0].subthemes) == 2
    assert (
        parse_codebook_markdown(
            """
| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Theme |  | Alpha | Desc | keep | drop |
| T2 | Theme 2 |  | Beta | Desc | keep | drop |
"""
        )
        is None
    )
    recovered = recover_exportable_codebook(
        State(
            {
                "messages": [
                    {"role": "assistant", "content": "nonsense"},
                    {
                        "role": "assistant",
                        "content": "## T9: Recovered\n- `C9` **Beta**: Body\n",
                    },
                ]
            }
        )
    )
    assert recovered is not None
    monkeypatch.setattr(
        codebook_module,
        "assistant_message_texts",
        lambda state: [
            "nonsense",
            "## T9: Recovered\n- `C9` **Beta**: Body\n",
        ],
    )
    assert recover_exportable_codebook(State({})) is not None

    # Insights.
    soft_report = ReportData(
        approved_codebook=codebook,
        units=[
            Unit(
                unit_id="U0001",
                record_id="R1",
                source="survey",
                text="Clear setup",
                original_text="Clear setup",
                metadata={"plan": "pro"},
            ),
            Unit(
                unit_id="U0002",
                record_id="R2",
                source="survey",
                text="Helpful flow",
                original_text="Helpful flow",
                metadata={"plan": "pro"},
            ),
            Unit(
                unit_id="U0003",
                record_id="R3",
                source="survey",
                text="Clear and helpful",
                original_text="Clear and helpful",
                metadata={"plan": "pro"},
            ),
        ],
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U0002",
                assignments=[
                    CodeAssignmentEntry(
                        code_id="C1", evidence="clear", sentiment="positive"
                    )
                ],
            ),
            CodeAssignment(
                unit_id="U0003",
                assignments=[
                    CodeAssignmentEntry(
                        code_id="C1", evidence="clear", sentiment="positive"
                    )
                ],
            ),
        ],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Theme remains visible",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="high",
            )
        ],
        segment_comparisons=[
            SegmentComparison(
                segment="plan",
                theme_id="T1",
                high_value="pro",
                low_value="basic",
                high_pct=75.0,
                low_pct=25.0,
                delta_pct=50.0,
                signal="weak",
                note="Weak split",
            )
        ],
    )
    critiqued = critique_insights(soft_report)
    assert critiqued[0].evidence_strength == "high"
    assert any("Weak segment difference" in note for note in critiqued[0].critic_notes)

    # Source parsing and payload normalisation.
    assert SourceParser.sniff_type(None, "{ }") == "transcript"
    assert SourceParser.sniff_type("note.docx", "hello") == "transcript"
    assert SourceParser.sniff_type(None, "subject,body\n1,hello") == "support_tickets"
    assert SourceParser.parse_survey_csv("", flexible_columns=False) == []
    assert (
        SourceParser.parse_survey_csv("comment\nhello\n", flexible_columns=True)[
            0
        ].record_id
        == "R00001"
    )
    assert (
        SourceParser.parse_transcript('[{"text": "Hello", "speaker": "A"}]')[0].speaker
        == "A"
    )
    assert (
        SourceParser.parse_transcript_json(
            '[1, {"text": "Hello", "speaker": "A", "meta": 1}]'
        )[0].speaker
        == "A"
    )
    assert SourceParser.parse_support_tickets("bad-json") == []
    assert (
        SourceParser.parse_chat_log(
            '{"messages": [1, {"text": "Hi", "role": "agent"}]}'
        )[0].source
        == "chat_log:agent"
    )
    support = SourceParser.parse_support_tickets(
        '{"tickets": [1, {"description": "", "requester": "A"}, {"message": "Help", "requester": "B"}]}'
    )
    assert len(support) == 1 and support[0].speaker == "B"
    assert (
        SourceParser.normalise_payload(
            State({"inputs": {"documents": [{"filename": "ignored"}]}}),
            {
                "configurable": {
                    "source": "id,text\n1,hello\n",
                    "source_filename": "x.csv",
                }
            },
        )["filename"]
        == "x.csv"
    )
    assert (
        SourceParser.normalise_payload(
            State({"inputs": {"documents": [{"content": "", "storage_path": ""}]}}),
            {
                "configurable": {
                    "source": "id,text\n1,hello\n",
                    "source_filename": "x.csv",
                }
            },
        )["filename"]
        == "x.csv"
    )
    assert (
        SourceParser.normalise_payload(
            State({"inputs": {"documents": [1]}}),
            {
                "configurable": {
                    "source": "id,text\n1,hello\n",
                    "source_filename": "x.csv",
                }
            },
        )["filename"]
        == "x.csv"
    )
    assert (
        SourceParser.normalise_payload(
            State({"inputs": "bad"}),
            {
                "configurable": {
                    "source": "id,text\n1,hello\n",
                    "source_filename": "x.csv",
                }
            },
        )["filename"]
        == "x.csv"
    )

    # Report validation and export failure branches.
    report_data = ReportData(
        approved_codebook=codebook,
        units=units,
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U0001",
                assignments=[CodeAssignmentEntry(code_id="C1", evidence="clear")],
            )
        ],
        quantification=[
            QuantificationRow(
                theme_id="missing",
                title="Missing",
                mentions=1,
                respondents=1,
                pct_respondents=100.0,
            )
        ],
        selected_quotes=[Quote(theme_id="missing", unit_id="missing", text="x")],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="obs",
                supporting_codes=["C3"],
                supporting_units=["missing"],
            )
        ],
        approved_insight_ids=["I1"],
    )
    errors = validate_final_state(report_data)
    assert any("unknown unit_id" in err for err in errors)
    assert any("unreportable code_id" in err for err in errors)
    unknown_code_errors = validate_final_state(
        ReportData(
            approved_codebook=codebook,
            units=units,
            candidate_insights=[
                CandidateInsight(
                    insight_id="I2",
                    observation="obs",
                    supporting_codes=["missing"],
                    supporting_units=["U0001"],
                )
            ],
            approved_insight_ids=["I2"],
        )
    )
    assert any("unknown code_id" in err for err in unknown_code_errors)

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("offline")

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/file.csv")

    async def _upload_blank(*args, **kwargs):  # noqa: ARG001
        return (None, "")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.report.upload_attachment", _upload_blank
    )
    report_output = ReportOutputNode(name="report_output")
    report_out = await report_output(
        State(
            {
                "results": {
                    "ingest": {"halt": False},
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "recommendation_generator": {
                        "candidate_insights": [
                            i.model_dump(mode="json")
                            for i in report_data.candidate_insights
                        ],
                        "approved_insight_ids": ["I1"],
                    },
                }
            }
        ),
        {},
    )
    assert "Download insight_report.md" not in report_out["assistant_message"]

    export_report = ExportReportNode(name="export_report")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.report.upload_attachment", _upload_fail
    )
    export_report_out = await export_report(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "recommendation_generator": {
                        "candidate_insights": [
                            i.model_dump(mode="json")
                            for i in report_data.candidate_insights
                        ],
                        "approved_insight_ids": ["I1"],
                    },
                }
            }
        ),
        {},
    )
    assert "Export failed" in export_report_out["assistant_message"]

    # Quantification and coded-data ingest fallbacks.
    coded_csv = (
        "unit_id,record_id,source,text,assignment_index,code_id,theme_id,theme_title\n"
        "U1,R1,s,hello,1,,T1,Theme\n"
    )
    coded_ingest = CodedDataIngestNode(name="coded_ingest")
    missing_assignments = await coded_ingest(
        State({"results": {"setup": {"source_payload": {"content": coded_csv}}}}),
        {},
    )
    assert missing_assignments["results"]["coded_ingest"]["halt"] is True

    # Pipeline branches.
    class _Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return _Attachment("café".encode("latin-1"), "attached.txt")

    attached_file = tmp_path / "attached.txt"
    attached_file.write_text("hello", encoding="utf-8")
    context_pre = ContextPreNode(name="context_pre")
    context_out = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [
                        {"attachment_id": "a1"},
                        {"storage_path": str(attached_file)},
                    ]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert "attached.txt" in context_out["results"]["context_pre"]["source_hint"]

    setup = SetupNode(name="setup", source_kind="coded_data")
    setup_out = await setup(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "coded_data.csv", "content": coded_csv}
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert "source_payload" in setup_out["results"]["setup"]

    excluded = SetupNode(name="setup", exclude_codebook_docs=True)
    excluded_out = await excluded(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "skip.csv", "content": ""},
                            {
                                "filename": "codebook.csv",
                                "content": (
                                    "theme_id,theme_title,code_id,code_title,definition\n"
                                    "T1,Theme,C1,Alpha,Desc\n"
                                ),
                            },
                            {"filename": "survey.csv", "content": "id,text\n1,Hello\n"},
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert (
        excluded_out["results"]["setup"]["source_payload"]["filename"] == "survey.csv"
    )

    raw_fallback = await SetupNode(name="setup")(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "inline.txt", "content": "hello there"}
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert (
        raw_fallback["results"]["setup"]["source_payload"]["filename"] == "inline.txt"
    )

    validator = FileValidatorNode(
        name="validate_files",
        data_file_kind="auto",
        single_data_file=True,
        require_codebook=True,
        codebook_result_field="approved_codebook",
        coded_data_result_field="coded_payload",
        seed_codebook_result_field="seed_codebook_from_file",
        announce_seed_codebook=True,
        missing_data_message="Need data.",
    )
    assert (
        "Need data."
        in (
            await validator(
                State(
                    {
                        "results": {
                            "context_pre": {
                                "pending_documents": [
                                    {"filename": "empty.txt", "content": ""}
                                ]
                            }
                        }
                    }
                ),
                {},
            )
        )["assistant_message"]
    )

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: (
            [object(), object()]
            if payload.get("filename") in {"data1.csv", "data2.csv"}
            else ([object()] if payload.get("filename") == "survey.csv" else []),
            "survey_csv",
        ),
    )
    validated = await validator(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "data1.csv", "content": "id,text\n1,Hello\n"},
                            {"filename": "data2.csv", "content": "id,text\n2,Hi\n"},
                            {"filename": "coded.csv", "content": coded_csv},
                            {
                                "filename": "codebook1.csv",
                                "content": (
                                    "theme_id,theme_title,code_id,code_title,definition\n"
                                    "T1,Theme,C1,Alpha,Desc\n"
                                ),
                            },
                            {
                                "filename": "codebook2.csv",
                                "content": (
                                    "theme_id,theme_title,code_id,code_title,definition\n"
                                    "T2,Theme,C2,Beta,Desc\n"
                                ),
                            },
                            {
                                "filename": "broken.txt",
                                "content": "",
                                "load_error": "broken",
                            },
                            {"filename": "survey.csv", "content": "id,text\n3,Third\n"},
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert "Multiple data files" in validated["assistant_message"]
    assert "Multiple codebook CSV files" in validated["assistant_message"]
    assert (
        "Could not parse as a data file or codebook"
        not in validated["assistant_message"]
    )
    nested = validated["results"]["validate_files"]
    assert nested["coded_payload"]["filename"] == "coded.csv"
    assert nested["approved_codebook"]["themes"]
    assert nested["seed_codebook_from_file"]["themes"]

    codebook_output = CodebookOutputNode(
        name="codebook_output", max_coding_batches=1, default_batch_size=1
    )
    assert (await codebook_output(State({"results": {"ingest": {"halt": False}}}), {}))[
        "assistant_message"
    ] == codebook_output.no_codebook_message

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    export_codebook = ExportCodebookNode(name="export_codebook")
    assert (
        "Export failed"
        in (
            await export_codebook(
                State(
                    {
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "## T1: Theme\n- `C1` **Alpha**: Desc\n",
                            }
                        ]
                    }
                ),
                {},
            )
        )["assistant_message"]
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    export_codebook_ok = await export_codebook(
        State(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "## T1: Theme\n- `C1` **Alpha**: Desc\n",
                    }
                ]
            }
        ),
        {},
    )
    assert "Download codebook.csv" in export_codebook_ok["assistant_message"]

    recode = RecodeOutputNode(name="recode_data")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    recode_out = await recode(
        State(
            {
                "results": {
                    "ingest": {"halt": False},
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "data_quality": {
                        "quality_report": {
                            "total_units": 2,
                            "flagged_units": 1,
                            "excluded_units": 0,
                        }
                    },
                    "recoder_finalize": {"total_batches": 2, "batch_end_index": 1},
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "Could not generate the download link" in recode_out["assistant_message"]
    assert "Quality:" in recode_out["assistant_message"]
    assert "only the first 1 unit(s) were coded" in recode_out["assistant_message"]

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    export_coded = ExportCodedDataNode(name="export_coded_data")
    exported_coded = await export_coded(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {},
    )
    assert "Download coded_data.csv" in exported_coded["assistant_message"]
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    assert (
        "Export failed"
        in (
            await export_coded(
                State(
                    {
                        "results": {
                            "setup": {
                                "approved_codebook": codebook.model_dump(mode="json")
                            },
                            "ingest": {
                                "units": [u.model_dump(mode="json") for u in units]
                            },
                            "open_coder_finalize": {
                                "code_assignments_pass1": [
                                    a.model_dump(mode="json") for a in assignments
                                ]
                            },
                        }
                    }
                ),
                {},
            )
        )["assistant_message"]
    )

    # Direct pipeline helper branches.
    class _Utf8Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return _Attachment("hello".encode("utf-8"), "utf8.txt")

    pipeline_context = ContextPreNode(name="context_pre")
    context_pending = await pipeline_context(
        State({"inputs": {"documents": [1]}}),
        {},
    )
    assert (
        context_pending["results"]["context_pre"]["source_hint"]
        == "No files loaded yet."
    )
    utf8_context = await pipeline_context(
        State(
            {
                "inputs": {
                    "documents": [
                        {"attachment_id": "a1", "filename": "given.txt"},
                        {"storage_path": str(tmp_path / "storage.txt")},
                    ]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _Utf8Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert "given.txt" in utf8_context["results"]["context_pre"]["source_hint"]

    setup_direct = SetupNode(name="setup")
    objective_result: dict[str, Any] = {}
    setup_direct._resolve_objective(
        State({"inputs": "bad"}),
        {"configurable": {"research_objective": "Identify onboarding friction"}},
        objective_result,
    )
    assert objective_result["objective"]
    objective_result = {}
    setup_direct._resolve_objective(
        State({"inputs": {"research_objective": "  Identify onboarding friction  "}}),
        {"configurable": {}},
        objective_result,
    )
    assert objective_result["objective"]
    latin_file = tmp_path / "latin1.txt"
    latin_file.write_bytes("café".encode("latin-1"))

    class LatinAttachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _LatinResolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return LatinAttachment("café".encode("latin-1"), "latin.txt")

    context_pre = ContextPreNode(name="context_pre")
    latin_context = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [
                        {"attachment_id": "a1"},
                        {"storage_path": str(latin_file)},
                    ]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _LatinResolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert "latin.txt" in latin_context["results"]["context_pre"]["source_hint"]
    assert (
        "café"
        in latin_context["results"]["context_pre"]["pending_documents"][0]["content"]
    )

    class Utf8Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _Utf8Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return Utf8Attachment(b"hello", "utf8.txt")

    utf8_context = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [{"attachment_id": "a2", "filename": "named.txt"}]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _Utf8Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert "named.txt" in utf8_context["results"]["context_pre"]["source_hint"]
    storage_only = tmp_path / "storage_only.txt"
    storage_only.write_bytes("café".encode("latin-1"))
    storage_context = await context_pre(
        State({"inputs": {"documents": [{"storage_path": str(storage_only)}]}}),
        {},
    )
    assert "file(s) loaded" in storage_context["results"]["context_pre"]["source_hint"]
    simple_attachment = await context_pre(
        State({"inputs": {"documents": [{"attachment_id": "a3"}]}}),
        {
            "configurable": {
                "attachment_resolver": _Utf8Resolver(),
                "attachment_scope": object(),
            }
        },
    )
    assert "utf8.txt" in simple_attachment["results"]["context_pre"]["source_hint"]
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: (
            [
                SimpleNamespace(
                    record_id="R1",
                    source="survey_csv",
                    speaker=None,
                    text="Hello there",
                    metadata={},
                )
            ],
            "survey_csv",
        )
        if payload and payload.get("content") == "id,text\n1,Hello\n"
        else ([], "survey_csv"),
    )
    assert (
        SetupNode(name="setup", source_kind="coded_data")._resolve_source(
            State(
                {
                    "results": {
                        "context_pre": {
                            "pending_documents": [
                                {"filename": "bad.csv", "content": "hello"}
                            ]
                        }
                    }
                }
            ),
            {},
        )
        is None
    )
    resolved_source = setup_direct._resolve_source(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "codebook.csv", "content": ""},
                            {
                                "filename": "survey.csv",
                                "content": "id,text\n1,Hello\n",
                            },
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert resolved_source is not None
    coded_source = SetupNode(name="setup", source_kind="coded_data")._resolve_source(
        State(
            {
                "results": {
                    "context_pre": {
                        "pending_documents": [
                            {"filename": "coded.csv", "content": "hello"}
                        ]
                    }
                }
            }
        ),
        {},
    )
    assert coded_source is None

    setup_exclude = SetupNode(name="setup", exclude_codebook_docs=True)
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.normalise_payload",
        lambda *args, **kwargs: None,
    )
    assert (
        setup_exclude._resolve_source(
            State(
                {
                    "results": {
                        "context_pre": {
                            "pending_documents": [
                                {
                                    "filename": "codebook.csv",
                                    "content": (
                                        "theme_id,theme_title,code_id,code_title,definition\n"
                                        "T1,Theme,C1,Alpha,Desc\n"
                                    ),
                                },
                                {
                                    "filename": "survey.csv",
                                    "content": "id,text\n1,Hello\n",
                                },
                            ]
                        }
                    }
                }
            ),
            {},
        )
        is not None
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: (
            [
                SimpleNamespace(
                    record_id="R2",
                    source="survey_csv",
                    speaker=None,
                    text="Hello there",
                    metadata={},
                )
            ],
            "survey_csv",
        )
        if payload and payload.get("content") == "id,text\n1,Hello\n"
        else ([], "survey_csv"),
    )
    assert (
        setup_exclude._resolve_source(
            State(
                {
                    "results": {
                        "context_pre": {
                            "pending_documents": [
                                {
                                    "filename": "codebook.csv",
                                    "content": (
                                        "theme_id,theme_title,code_id,code_title,definition\n"
                                        "T1,Theme,C1,Alpha,Desc\n"
                                    ),
                                },
                                {
                                    "filename": "survey.csv",
                                    "content": "id,text\n1,Hello\n",
                                },
                            ]
                        }
                    }
                }
            ),
            {},
        )
        is not None
    )

    setup_run = SetupNode(name="setup", resolve_objective=False)
    source_state = State(
        {
            "inputs": {
                "documents": [
                    {"filename": "survey.csv", "content": "id,text\n1,Hello\n"}
                ]
            }
        }
    )
    setup_run_result = await setup_run(source_state, {})
    assert isinstance(setup_run_result, dict)
    await setup_run(
        State(
            {
                "results": {
                    "setup": {
                        "source_payload": {
                            "content": "id,text\n1,Hello\n",
                            "filename": "survey.csv",
                            "source_type": "survey_csv",
                        }
                    }
                }
            }
        ),
        {},
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.normalise_payload",
        lambda *args, **kwargs: None,
    )
    assert (
        setup_run._resolve_source(
            State(
                {
                    "results": {
                        "context_pre": {
                            "pending_documents": [
                                {"filename": "inline.txt", "content": "hello"}
                            ]
                        }
                    }
                }
            ),
            {},
        )
        is not None
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.normalise_payload",
        lambda state, config=None, **kwargs: (
            {
                "content": "id,text\n1,Hello\n",
                "filename": "inline.txt",
                "source_type": "survey_csv",
                "storage_path": None,
            }
            if isinstance(state.get("inputs"), Mapping)
            and isinstance(state.get("inputs"), Mapping)
            and isinstance(state.get("inputs").get("documents"), list)
            and state.get("inputs").get("documents")
            else None
        ),
    )

    ingest_direct = IngestNode(name="ingest")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda source_payload, **kwargs: (
            [
                SimpleNamespace(
                    record_id="R1",
                    source="survey_csv",
                    speaker=None,
                    text="Hello there",
                    metadata={},
                )
            ],
            "survey_csv",
        )
        if source_payload
        else ([], "survey_csv"),
    )
    ingested_direct = await ingest_direct(
        State(
            {
                "results": {
                    "setup": {
                        "source_payload": {
                            "content": "id,text\n1,Hello there\n",
                            "filename": "survey.csv",
                            "source_type": "survey_csv",
                        }
                    }
                }
            }
        ),
        {},
    )
    assert ingested_direct["results"]["ingest"]["source_type"] == "survey_csv"
    ingested_from_input = await ingest_direct(
        State(
            {
                "inputs": {
                    "documents": [
                        {
                            "content": "id,text\n1,Hello there\n",
                            "filename": "survey.csv",
                        }
                    ]
                }
            }
        ),
        {},
    )
    assert ingested_from_input["results"]["ingest"]["unit_count"] == 1
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda *args, **kwargs: (
            [
                SimpleNamespace(
                    record_id="R2",
                    source="survey_csv",
                    speaker=None,
                    text="Hello there",
                    metadata={},
                )
            ],
            "survey_csv",
        ),
    )
    ingested_without_source = await ingest_direct(State({"results": {}}), {})
    assert ingested_without_source["results"]["ingest"]["unit_count"] == 1

    validator_raw = FileValidatorNode(name="validate_raw", data_file_kind="raw")
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda *args, **kwargs: ([], "unsupported"),
    )
    assert validator_raw._classify("hello", "unknown.bin")[0] == "unknown"
    validator_auto_raw = FileValidatorNode(
        name="validate_auto_raw", data_file_kind="auto"
    )
    assert validator_auto_raw._classify("hello", "unknown.bin")[0] == "unknown"

    validator_auto = FileValidatorNode(
        name="validate_auto",
        data_file_kind="auto",
        single_data_file=True,
        require_codebook=True,
        codebook_result_field="approved_codebook",
        coded_data_result_field="coded_payload",
        seed_codebook_result_field="seed_codebook_from_file",
        announce_seed_codebook=False,
    )
    valid_validator_state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"filename": "survey.csv", "content": "id,text\n1,Hello\n"},
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Alpha,Desc\n"
                            ),
                        },
                    ]
                }
            }
        }
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: ([object()], "survey_csv")
        if payload.get("filename") == "survey.csv"
        else ([], "survey_csv"),
    )
    clean_validation = await validator_auto(valid_validator_state, {})
    assert "Ready" in clean_validation["assistant_message"]
    assert clean_validation["results"]["validate_auto"]["approved_codebook"]

    duplicated_state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"filename": "data1.csv", "content": "id,text\n1,Hello\n"},
                        {"filename": "data2.csv", "content": "id,text\n2,Hi\n"},
                        {"filename": "coded.csv", "content": coded_csv},
                        {"filename": "coded2.csv", "content": coded_csv},
                        {
                            "filename": "codebook1.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Alpha,Desc\n"
                            ),
                        },
                    ]
                }
            }
        }
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.SourceParser.parse_payload",
        lambda payload, **kwargs: (
            [object(), object()]
            if payload.get("filename") in {"data1.csv", "data2.csv"}
            else [],
            "survey_csv",
        ),
    )
    dup_validation = await validator_auto(duplicated_state, {})
    assert "Multiple coded data files" in dup_validation["assistant_message"]

    existing_state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Alpha,Desc\n"
                            ),
                        }
                    ]
                },
                "validate_auto": {
                    "approved_codebook": codebook.model_dump(mode="json"),
                    "seed_codebook_from_file": codebook.model_dump(mode="json"),
                },
            }
        }
    )
    existing_validation = await validator_auto(existing_state, {})
    assert existing_validation["assistant_message"]
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.get_approved_codebook",
        lambda *args, **kwargs: codebook,
    )
    await validator_auto(existing_state, {})

    codebook_output = CodebookOutputNode(
        name="codebook_output",
        ingest_node_name="ingest",
        max_coding_batches=1,
        default_batch_size=1,
    )
    note_result = await codebook_output(
        State(
            {
                "results": {
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "per-run limit" in note_result["assistant_message"]
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.get_units",
        lambda *args, **kwargs: units + [units[0]],
    )
    assert (
        "per-run limit"
        in (
            await codebook_output(
                State(
                    {
                        "results": {
                            "ingest": {
                                "units": [u.model_dump(mode="json") for u in units]
                            },
                            "codebook_consolidator_finalize": {
                                "draft_codebook": codebook.model_dump(mode="json")
                            },
                        }
                    }
                ),
                {"configurable": {"batch_size": 1}},
            )
        )["assistant_message"]
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.get_units",
        lambda *args, **kwargs: [],
    )
    assert (
        "per-run limit"
        not in (
            await codebook_output(
                State(
                    {
                        "results": {
                            "ingest": {
                                "units": [u.model_dump(mode="json") for u in units]
                            },
                            "codebook_consolidator_finalize": {
                                "draft_codebook": codebook.model_dump(mode="json")
                            },
                        }
                    }
                ),
                {"configurable": {"batch_size": 1}},
            )
        )["assistant_message"]
    )

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    export_codebook_persist = await export_codebook(
        State(
            {
                "results": {
                    "export_codebook": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    }
                }
            }
        ),
        {},
    )
    assert (
        "results" not in export_codebook_persist
        or "export_codebook" not in export_codebook_persist.get("results", {})
    )

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    recode_success = await recode(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "recoder_finalize": {"total_batches": 1, "batch_end_index": 1},
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "Download coded_data.csv" in recode_success["assistant_message"]
    assert "Quality:" not in recode_success["assistant_message"]
    assert "only the first" not in recode_success["assistant_message"]

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.build_coded_data_csv",
        lambda *args, **kwargs: ("", 0),
    )
    recode_empty_csv = await recode(
        State(
            {
                "results": {
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert recode_empty_csv["assistant_message"]

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.build_coded_data_csv",
        lambda *args, **kwargs: ("csv", 1),
    )
    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    recode_no_note = await recode(
        State(
            {
                "results": {
                    "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                    "recoder_finalize": {"total_batches": 1, "batch_end_index": 1},
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "Download coded_data.csv" in recode_no_note["assistant_message"]

    recode_no_codebook = await recode(
        State(
            {
                "results": {
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "open_coder_finalize": {
                        "code_assignments_pass1": [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert recode_no_codebook["assistant_message"]

    # Stage dispatch and finalization fallbacks.
    prepare_unknown = LLMStagePrepareNode.model_construct(
        name="unknown_prepare", stage="unknown"
    )
    assert (await prepare_unknown(State({"results": {}}), {}))["results"][
        "unknown_prepare"
    ]["done"]
    prepare_recoder = LLMStagePrepareNode(
        name="recoder_prepare",
        stage="recoder",
        max_coding_batches=1,
    )
    recoder_prompt = await prepare_recoder(
        State(
            {
                "results": {
                    "setup": {
                        keys.approved_codebook_field: codebook.model_dump(mode="json")
                    },
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                    "recoder_finalize": {"next_index": 2},
                }
            }
        ),
        {"configurable": {"batch_size": 1, "per_turn_batch_budget": 1}},
    )
    assert recoder_prompt["results"]["recoder_prepare"]["done"]

    class Payload(BaseModel):
        value: int

    extractor = LLMStageFinalizeNode(name="extract", stage="open_coder")
    assert extractor._extract_llm_response("x", None) is None
    assert extractor._extract_llm_response({}, None) is None
    assert extractor._extract_llm_response({"messages": ["hello"]}, None) is None
    assert (
        extractor._extract_llm_response(
            {"messages": [AIMessage(content="hello")]}, None
        )
        == "hello"
    )
    assert extractor._extract_llm_response(
        {"structured_response": {"value": 2}}, Payload
    ) == Payload(value=2)
    assert (
        extractor._extract_llm_response({"structured_response": "bad"}, Payload) is None
    )

    finalize_open = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        response_schema=OpenCodingBatchResponse,
    )
    assert finalize_open._finalize_open_coder(
        State(
            {
                "results": {
                    "ingest": {
                        keys.units_field: [u.model_dump(mode="json") for u in units]
                    },
                    "open_coder_finalize": {"code_assignments_pass1": []},
                }
            }
        ),
        {"batch_index": 99, "total_batches": 1, "batch_size": 1},
    )["done"]
    open_state = State(
        {
            "structured_response": OpenCodingBatchResponse(
                assignments=[
                    CodeAssignment(unit_id="U0001", assignments=[]),
                    CodeAssignment(
                        unit_id="U0001",
                        assignments=[
                            CodeAssignmentEntry(
                                code_id="C1", evidence="clear", confidence=0.9
                            )
                        ],
                    ),
                ]
            ).model_dump(mode="json"),
            "results": {
                "ingest": {
                    keys.units_field: [u.model_dump(mode="json") for u in units]
                },
                "open_coder_finalize": {"code_assignments_pass1": []},
            },
        }
    )
    assert (
        finalize_open._finalize_open_coder(
            open_state,
            {"batch_index": 0, "total_batches": 1, "batch_size": 1},
        )["continue_llm"]
        is False
    )

    finalize_consolidator = LLMStageFinalizeNode(
        name="codebook_consolidator_finalize",
        stage="codebook_consolidator",
    )
    merged = finalize_consolidator._finalize_consolidator(
        State(
            {
                "results": {
                    "open_coder_finalize": {
                        keys.assignments_field: [
                            a.model_dump(mode="json") for a in assignments
                        ]
                    }
                }
            }
        ),
        {"configurable": {"seed_codebook": codebook.model_dump(mode="json")}},
        {"action": "merge"},
    )
    assert "draft_codebook" in merged

    finalize_quote = LLMStageFinalizeNode(
        name="quote_selector_finalize", stage="quote_selector"
    )
    assert (
        finalize_quote._finalize_quote_selector(State({"results": {}}), {})["quotes"]
        == 0
    )

    finalize_insight = LLMStageFinalizeNode(
        name="insight_generator_finalize",
        stage="insight_generator",
    )
    assert (
        finalize_insight._finalize_insight_generator(State({"results": {}}))["halt"]
        is False
    )
