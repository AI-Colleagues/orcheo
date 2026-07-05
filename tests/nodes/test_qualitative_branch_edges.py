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
from orcheo.nodes.logic import StructuredRouterDispatchNode
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
    CodebookOutputNode,
    ExportCodebookNode,
    ExportCodedDataNode,
    IngestNode,
    RecodeOutputNode,
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
