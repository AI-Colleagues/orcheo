from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from orcheo.graph.state import State
from orcheo.nodes.logic import (
    ExtractAIMessageNode,
    StructuredRouterDispatchNode,
)
from orcheo.nodes.qualitative.accessors import (
    _coerce_model,
    _coerce_models,
    build_report_data,
    get_approved_codebook,
    get_approved_insight_ids,
    get_candidate_insights,
    get_code_assignments,
    get_configurable,
    get_cooccurrence,
    get_draft_codebook,
    get_pending_documents,
    get_quality_report,
    get_quantification,
    get_recommendations,
    get_research_objective,
    get_seed_codebook_from_file,
    get_segment_breakdowns,
    get_segment_comparisons,
    get_selected_quotes,
    get_source_payload,
    get_units,
    is_vacuous,
)
from orcheo.nodes.qualitative.codebook import (
    code_to_theme_map,
    escape_markdown_table_cell,
    fallback_codebook,
    get_seed_codebook,
    make_code_id,
    make_insight_id,
    make_theme_id,
    make_unit_id,
    merge_codebooks,
    normalise_codebook_ids,
    normalise_label,
    parse_codebook_csv,
    parse_codebook_markdown,
    parse_markdown_table_row,
    recover_exportable_codebook,
    render_codebook_for_prompt,
)
from orcheo.nodes.qualitative.coded_data import (
    build_coded_data_csv,
    parse_coded_data_csv,
)
from orcheo.nodes.qualitative.coding import (
    batch_units,
    existing_code_hints,
    filter_assignments_to_codebook,
    format_assignments_with_units,
    format_open_coding_user_text,
    format_recoding_user_text,
    with_inferred_sentiment,
)
from orcheo.nodes.qualitative.insights import (
    critique_insights,
    fallback_insights,
    fallback_quotes,
    filter_grounded_quotes,
    normalise_candidate_insights,
    recommend_action,
    recommend_impact,
)
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    CooccurrenceRow,
    ParsedRecord,
    QualityReport,
    QuantificationRow,
    Quote,
    Recommendation,
    ReportData,
    SegmentBreakdownRow,
    SegmentComparison,
    Subtheme,
    Theme,
    Unit,
)
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
from orcheo.nodes.qualitative.sources import (
    SourceParser,
    pick_id_field,
    pick_text_field,
)
from orcheo.runtime.results import (
    assistant_message_texts,
    first_result_field,
    node_result,
    results_map,
)


def _codebook() -> Codebook:
    return Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Onboarding",
                subthemes=[
                    Subtheme(
                        code_id="C1",
                        title="Clear setup",
                        definition="Easy to follow",
                    ),
                    Subtheme(
                        code_id="C2",
                        title="Hard auth",
                        definition="Confusing",
                    ),
                ],
            ),
            Theme(
                theme_id="T2",
                title="Support",
                subthemes=[
                    Subtheme(
                        code_id="C3",
                        title="Responsive",
                        definition="Fast help",
                    )
                ],
            ),
        ]
    )


def _units() -> list[Unit]:
    return [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="survey",
            speaker=None,
            text="The setup was clear and quick.",
            original_text="The setup was clear and quick.",
            metadata={"segment": "A", "plan": "pro"},
        ),
        Unit(
            unit_id="U0002",
            record_id="R2",
            source="survey",
            speaker="Ana",
            text="The login was confusing but helpful.",
            original_text="The login was confusing but helpful.",
            metadata={"segment": "B", "plan": "basic"},
        ),
    ]


def _assignments() -> list[CodeAssignment]:
    return [
        CodeAssignment(
            unit_id="U0001",
            assignments=[
                CodeAssignmentEntry(
                    code_id="C1", evidence="clear", confidence=0.9, sentiment="positive"
                ),
                CodeAssignmentEntry(
                    code_id="C3", evidence="quick", confidence=0.8, sentiment="positive"
                ),
            ],
        ),
        CodeAssignment(
            unit_id="U0002",
            assignments=[
                CodeAssignmentEntry(
                    code_id="C2",
                    evidence="confusing",
                    confidence=0.7,
                    sentiment="negative",
                )
            ],
        ),
    ]


def _report_state() -> ReportData:
    return ReportData(
        research_objective="Understand onboarding",
        pending_documents=[{"filename": "survey.csv"}],
        source_payload={"filename": "survey.csv"},
        units=_units(),
        approved_codebook=_codebook(),
        code_assignments_pass2=_assignments(),
        quantification=[
            QuantificationRow(
                theme_id="T1",
                title="Onboarding",
                mentions=2,
                respondents=2,
                pct_respondents=100.0,
                sentiment_counts={"positive": 2},
            ),
            QuantificationRow(
                theme_id="T2",
                title="Support",
                mentions=1,
                respondents=1,
                pct_respondents=50.0,
            ),
        ],
        cooccurrence=[
            CooccurrenceRow(theme_id_a="T1", theme_id_b="T2", respondents=1, mentions=1)
        ],
        segment_breakdowns=[
            SegmentBreakdownRow(
                segment="plan",
                value="pro",
                theme_id="T1",
                respondents=1,
                total_respondents=1,
                pct_respondents=100.0,
                sample_size_guard="ok",
            ),
            SegmentBreakdownRow(
                segment="plan",
                value="basic",
                theme_id="T1",
                respondents=0,
                total_respondents=1,
                pct_respondents=0.0,
                sample_size_guard="ok",
            ),
        ],
        segment_comparisons=[
            SegmentComparison(
                segment="plan",
                theme_id="T1",
                high_value="pro",
                low_value="basic",
                high_pct=100.0,
                low_pct=0.0,
                delta_pct=100.0,
                signal="strong",
                note="Pro users mention onboarding more often.",
            )
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
                insight_id="I01",
                observation="Onboarding is clear",
                interpretation="Users can get started quickly.",
                implication="Keep the flow simple.",
                supporting_codes=["C1", "C3"],
                supporting_units=["U0001"],
                evidence_strength="high",
            )
        ],
        recommendations=[
            Recommendation(
                insight_id="I01",
                finding="Onboarding is clear",
                action="Keep the flow simple.",
                expected_impact="Reduce friction.",
            )
        ],
        approved_insight_ids=["I01"],
    )


def test_runtime_result_helpers_cover_empty_and_nested_paths() -> None:
    state = State({"node_results": {"a": {"value": 1}, "b": "not-a-mapping"}})

    assert results_map(state)["a"]["value"] == 1
    assert node_result(state, "a") == {"value": 1}
    assert node_result(state, "b") == {}
    assert first_result_field(state, "value", ("missing", "a")) == 1
    assert first_result_field(state, "value", ("missing",)) is None
    assert assistant_message_texts(
        State(
            {
                "messages": [
                    {"role": "user", "content": "ignore"},
                    {"type": "assistant", "content": "hello"},
                    AIMessage(content="world"),
                ]
            }
        )
    ) == ["world", "hello"]
    assert assistant_message_texts(State({"messages": "bad"})) == []


def test_routing_node_decision_paths() -> None:
    class Decision(BaseModel):
        action: str = "respond"
        branch: str = "fallback"
        message: str = "Direct response"
        topic: str = "testing"

    router = StructuredRouterDispatchNode(
        name="router",
        carried_fields=["topic", "missing"],
    )
    assert router._decision_value({"action": "route"}, "action") == "route"
    assert router._decision_value(Decision(), "branch") == "fallback"
    assert router._decision_value(SimpleNamespace(message="x"), "message") == "x"


@pytest.mark.asyncio
async def test_routing_nodes_route_and_respond() -> None:
    router = StructuredRouterDispatchNode(
        name="router",
        carried_fields=["topic", "note"],
        assistant_message_fallback="fallback",
    )
    routed = await router(
        State(
            {"structured_response": {"action": "route", "branch": "next", "topic": "t"}}
        ),
        {},
    )
    assert routed["node_results"]["router"] == {"topic": "t", "routing": "next"}

    responded = await router(
        State(
            {
                "structured_response": {
                    "action": "respond",
                    "message": "",
                    "topic": "t",
                    "note": "",
                }
            }
        ),
        {},
    )
    assert responded["assistant_message"] == "fallback"
    assert responded["node_results"]["router"]["routing"] == "respond"

    extractor = ExtractAIMessageNode(
        name="extract_ai_message",
        fallback_message="fallback",
    )
    assert (await extractor(State({}), {}))["assistant_message"] == "fallback"
    assert (
        await extractor(
            State({"structured_response": {"assistant_message": "done"}}), {}
        )
    )["assistant_message"] == "done"


def test_accessor_helpers_and_report_data_cover_all_accessors() -> None:
    keys = QualitativeResultKeys()
    state = State(
        {
            "node_results": {
                "setup": {
                    keys.research_objective_field: "Understand onboarding",
                    keys.source_payload_field: {"filename": "survey.csv"},
                    keys.pending_documents_field: [
                        {"filename": "survey.csv", "content": "hello"},
                        "bad",
                    ],
                    keys.approved_codebook_field: _codebook().model_dump(mode="json"),
                    keys.units_field: [u.model_dump(mode="json") for u in _units()]
                    + ["bad"],
                    keys.assignments_field: [
                        a.model_dump(mode="json") for a in _assignments()
                    ],
                    keys.draft_codebook_field: _codebook().model_dump(mode="json"),
                    keys.quality_report_field: {
                        "total_units": 1,
                        "flagged_units": 1,
                        "excluded_units": 0,
                    },
                    keys.quantification_field: [
                        {
                            "theme_id": "T1",
                            "title": "Onboarding",
                            "mentions": 1,
                            "respondents": 1,
                            "pct_respondents": 100.0,
                        }
                    ],
                    keys.cooccurrence_field: [
                        {
                            "theme_id_a": "T1",
                            "theme_id_b": "T2",
                            "respondents": 1,
                            "mentions": 1,
                        }
                    ],
                    keys.segment_breakdowns_field: [
                        {
                            "segment": "plan",
                            "value": "pro",
                            "theme_id": "T1",
                            "respondents": 1,
                            "total_respondents": 1,
                            "pct_respondents": 100.0,
                            "sample_size_guard": "ok",
                        }
                    ],
                    keys.segment_comparisons_field: [
                        {
                            "segment": "plan",
                            "theme_id": "T1",
                            "high_value": "pro",
                            "low_value": "basic",
                            "high_pct": 100.0,
                            "low_pct": 0.0,
                            "delta_pct": 100.0,
                            "signal": "strong",
                            "note": "note",
                        }
                    ],
                    keys.selected_quotes_field: [
                        {"theme_id": "T1", "unit_id": "U0001", "text": "quote"}
                    ],
                    keys.candidate_insights_field: [
                        {
                            "insight_id": "I01",
                            "observation": "obs",
                            "supporting_codes": ["C1"],
                            "supporting_units": ["U0001"],
                        }
                    ],
                    keys.recommendations_field: [
                        {
                            "insight_id": "I01",
                            "finding": "obs",
                            "action": "act",
                            "expected_impact": "impact",
                        }
                    ],
                    keys.approved_insight_ids_field: [1, "I01"],
                },
                "context_pre": {
                    keys.pending_documents_field: [
                        {"filename": "survey.csv", "content": "hello"}
                    ]
                },
                "validate_files": {keys.seed_codebook_field: {"themes": []}},
                "ingest": {
                    keys.units_field: [u.model_dump(mode="json") for u in _units()],
                    keys.quantification_field: [
                        {
                            "theme_id": "T1",
                            "title": "Onboarding",
                            "mentions": 1,
                            "respondents": 1,
                            "pct_respondents": 100.0,
                        }
                    ],
                    keys.cooccurrence_field: [
                        {
                            "theme_id_a": "T1",
                            "theme_id_b": "T2",
                            "respondents": 1,
                            "mentions": 1,
                        }
                    ],
                    keys.segment_breakdowns_field: [
                        {
                            "segment": "plan",
                            "value": "pro",
                            "theme_id": "T1",
                            "respondents": 1,
                            "total_respondents": 1,
                            "pct_respondents": 100.0,
                            "sample_size_guard": "ok",
                        }
                    ],
                    keys.segment_comparisons_field: [
                        {
                            "segment": "plan",
                            "theme_id": "T1",
                            "high_value": "pro",
                            "low_value": "basic",
                            "high_pct": 100.0,
                            "low_pct": 0.0,
                            "delta_pct": 100.0,
                            "signal": "strong",
                            "note": "note",
                        }
                    ],
                },
                "open_coder_finalize": {
                    keys.assignments_field: [
                        a.model_dump(mode="json") for a in _assignments()
                    ]
                },
                "codebook_consolidator_finalize": {
                    keys.draft_codebook_field: _codebook().model_dump(mode="json")
                },
                "data_quality": {
                    keys.quality_report_field: {
                        "total_units": 1,
                        "flagged_units": 1,
                        "excluded_units": 0,
                    }
                },
                "quote_selector_finalize": {
                    keys.selected_quotes_field: [
                        {"theme_id": "T1", "unit_id": "U0001", "text": "quote"}
                    ]
                },
                "recommendation_generator": {
                    keys.candidate_insights_field: [
                        {
                            "insight_id": "I01",
                            "observation": "obs",
                            "supporting_codes": ["C1"],
                            "supporting_units": ["U0001"],
                        }
                    ],
                    keys.recommendations_field: [
                        {
                            "insight_id": "I01",
                            "finding": "obs",
                            "action": "act",
                            "expected_impact": "impact",
                        }
                    ],
                    keys.approved_insight_ids_field: [1, "I01"],
                },
            }
        }
    )

    assert get_configurable({"configurable": {"x": 1}}) == {"x": 1}
    assert get_configurable(None) == {}
    assert is_vacuous(" ") is True
    assert is_vacuous("one two") is True
    assert is_vacuous("one two three") is False
    assert get_research_objective(state, keys) == "Understand onboarding"
    assert get_source_payload(state, keys) == {"filename": "survey.csv"}
    assert get_pending_documents(state, keys) == [
        {"filename": "survey.csv", "content": "hello"}
    ]
    assert get_seed_codebook_from_file(state, keys) == {"themes": []}
    assert get_approved_codebook(state, keys) is not None
    assert len(get_units(state, keys)) == 2
    assert len(get_code_assignments(state, keys)) == 2
    assert get_draft_codebook(state, keys) is not None
    assert get_quality_report(state, keys) is not None
    assert len(get_quantification(state, keys)) == 1
    assert len(get_cooccurrence(state, keys)) == 1
    assert len(get_segment_breakdowns(state, keys)) == 1
    assert len(get_segment_comparisons(state, keys)) == 1
    assert len(get_selected_quotes(state, keys)) == 1
    assert len(get_candidate_insights(state, keys)) == 1
    assert len(get_recommendations(state, keys)) == 1
    assert get_approved_insight_ids(state, keys) == ["1", "I01"]
    data = build_report_data(state, keys)
    assert data.research_objective == "Understand onboarding"
    assert data.approved_insight_ids == ["1", "I01"]

    class Demo(BaseModel):
        value: int

    assert _coerce_models(["bad", {"value": 1}], Demo) == [Demo(value=1)]
    assert _coerce_model("bad", Demo) is None


def test_codebook_helpers_cover_generation_and_parsing() -> None:
    codebook = _codebook()
    assert make_unit_id(7) == "U0007"
    assert make_code_id(7) == "C007"
    assert make_theme_id(7) == "T07"
    assert make_insight_id(7) == "I07"
    assert normalise_label("  hard-auth_flow  ") == "hard auth flow"
    assert code_to_theme_map(codebook)["C1"] == ("T1", "Onboarding")
    assert render_codebook_for_prompt(codebook).startswith("T1: Onboarding")

    normalised = normalise_codebook_ids(
        Codebook(
            themes=[
                Theme(
                    theme_id="", title="A", subthemes=[Subtheme(code_id="", title="X")]
                ),
                Theme(
                    theme_id="",
                    title="B",
                    subthemes=[Subtheme(code_id="C099", title="Y")],
                ),
            ]
        )
    )
    assert [theme.theme_id for theme in normalised.themes] == ["T01", "T02"]
    assert [sub.code_id for sub in normalised.themes[0].subthemes] == ["C001"]
    assert [sub.code_id for sub in normalised.themes[1].subthemes] == ["C099"]

    merged = merge_codebooks(
        Codebook(
            themes=[
                Theme(
                    theme_id="T1",
                    title="Seed",
                    subthemes=[Subtheme(code_id="C001", title="Keep")],
                )
            ]
        ),
        Codebook(
            themes=[
                Theme(
                    theme_id="T2",
                    title="Emergent",
                    subthemes=[
                        Subtheme(code_id="C001", title="Keep"),
                        Subtheme(code_id="", title="New"),
                    ],
                )
            ]
        ),
    )
    assert [sub.code_id for theme in merged.themes for sub in theme.subthemes] == [
        "C001",
        "C002",
    ]

    fallback = fallback_codebook(
        [
            CodeAssignment(
                unit_id="U1",
                assignments=[
                    CodeAssignmentEntry(code_id="alpha_beta", evidence="Alpha beta"),
                    CodeAssignmentEntry(code_id="alpha_beta", evidence="Alpha beta"),
                    CodeAssignmentEntry(code_id="gamma", evidence="Gamma"),
                ],
            )
        ]
    )
    assert fallback.themes[0].subthemes[0].title == "alpha beta"
    assert fallback.themes[0].subthemes[0].example_quotes[0]["text"] == "Alpha beta"

    csv_text = (
        "theme_id,theme_title,code_id,code_title,definition,include,exclude\n"
        "T1,Onboarding,C1,Clear setup,Easy,one; two,none\n"
    )
    parsed = parse_codebook_csv(csv_text)
    assert parsed is not None
    assert parsed.themes[0].subthemes[0].include == ["one", "two"]
    assert (
        parse_codebook_csv(
            "theme_id,theme_title,code_id,code_title,definition,unit_id\nT1,A,C1,B,C,D\n",
            reject_coded_data=True,
        )
        is None
    )
    assert parse_codebook_csv("theme_id,theme_title\nT1,A\n") is None

    assert parse_markdown_table_row(r"| a \| b | c |") == ["a | b", "c"]
    assert escape_markdown_table_cell("a|b\n<c>") == "a&#124;b<br>&lt;c&gt;"

    markdown_table = (
        "| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| T1 | Onboarding | C1 | Clear setup | Easy | one; two | three |\n"
    )
    markdown = parse_codebook_markdown(markdown_table)
    assert markdown is not None
    assert markdown.themes[0].subthemes[0].code_id == "C1"

    headings = parse_codebook_markdown(
        "## T1: Onboarding\n- `C1` **Clear setup**: Easy to follow\n"
    )
    assert headings is not None
    assert headings.themes[0].subthemes[0].title == "Clear setup"
    assert parse_codebook_markdown("not a codebook") is None

    recovered = recover_exportable_codebook(
        State(
            {
                "node_results": {
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    }
                },
                "messages": [],
            }
        )
    )
    assert recovered is not None
    markdown_message = "## T1: Onboarding\n- `C1` **Clear setup**: Easy to follow\n"
    markdown_recovered = recover_exportable_codebook(
        State({"messages": [{"role": "assistant", "content": markdown_message}]})
    )
    assert markdown_recovered is not None

    assert (
        get_seed_codebook(
            {
                "configurable": {
                    "seed_codebook": json.dumps(codebook.model_dump(mode="json"))
                }
            }
        )
        is not None
    )
    assert (
        get_seed_codebook({"configurable": {"seed_codebook": {"themes": []}}})
        is not None
    )
    assert get_seed_codebook({"configurable": {"seed_codebook": "not-json"}}) is None
    assert (
        get_seed_codebook(
            None,
            state=State(
                {
                    "node_results": {
                        "validate_files": {
                            "seed_codebook_from_file": codebook.model_dump(mode="json")
                        }
                    }
                }
            ),
        )
        is not None
    )


def test_coded_data_helpers_cover_round_trip_and_branching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    csv_text, total = build_coded_data_csv(units, assignments, codebook)
    assert total == 3
    parsed = parse_coded_data_csv(csv_text)
    assert parsed is not None
    parsed_units, parsed_assignments, parsed_codebook = parsed
    assert len(parsed_units) == 2
    assert len(parsed_assignments) == 2
    assert parsed_codebook is not None

    assert parse_coded_data_csv("unit_id,text\nU1,plain\n") is None

    bad_csv = (
        "unit_id,record_id,source,speaker,text,original_text,metadata,quality_flags,"
        "assignment_index,code_id,theme_id,theme_title,code_title,definition,evidence,"
        "confidence,sentiment\n"
        "U1,R1,s,,text,text,not-json,,1,C1,T1,Onboarding,Clear,Easy,clear,not-a-number,weird\n"
    )
    parsed_bad = parse_coded_data_csv(bad_csv)
    assert parsed_bad is not None
    bad_units, bad_assignments, _ = parsed_bad
    assert bad_units[0].metadata == {}
    assert bad_assignments[0].assignments[0].confidence == 0.0
    assert bad_assignments[0].assignments[0].sentiment == "neutral"

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.coded_data.csv.DictReader",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    assert parse_coded_data_csv("anything") is None


def test_coding_helpers_cover_all_branch_variants() -> None:
    units = _units()
    assignments = _assignments()
    assert batch_units(units, 1) == [[units[0]], [units[1]]]
    assert (
        format_open_coding_user_text(units[:1])
        == "Units:\n- U0001: The setup was clear and quick."
    )
    assert (
        format_recoding_user_text(units[:1])
        == "Units:\n- U0001: The setup was clear and quick."
    )
    assert existing_code_hints(None) == []
    assert existing_code_hints(assignments, limit=1) == ["C1"]
    assert format_assignments_with_units(assignments, units, limit=1).startswith(
        "- U0001:"
    )
    assert (
        format_assignments_with_units([CodeAssignment(unit_id="missing")], units)
        == "(no assignments)"
    )
    neutral = CodeAssignmentEntry(code_id="C1", evidence="clear", sentiment="neutral")
    assert with_inferred_sentiment(neutral, None) is neutral
    assert (
        with_inferred_sentiment(
            neutral,
            Unit(
                unit_id="U1",
                record_id="R1",
                source="s",
                text="It was easy and helpful.",
                original_text="It was easy and helpful.",
            ),
        ).sentiment
        == "positive"
    )
    filtered = filter_assignments_to_codebook(
        [
            CodeAssignment(
                unit_id="U0001",
                assignments=[
                    CodeAssignmentEntry(code_id="C1", confidence=0.9),
                    CodeAssignmentEntry(code_id="missing", confidence=0.9),
                    CodeAssignmentEntry(code_id="C2", confidence=-1),
                ],
            )
        ],
        _codebook(),
        {u.unit_id: u for u in units},
        infer_sentiment=True,
    )
    assert [entry.code_id for entry in filtered[0].assignments] == ["C1"]


def test_quality_and_quantify_helpers_cover_edge_cases() -> None:
    units = [
        Unit(unit_id="U1", record_id="R1", source="s", text="", original_text=""),
        Unit(unit_id="U2", record_id="R1", source="s", text="hi", original_text="hi"),
        Unit(
            unit_id="U3",
            record_id="R1",
            source="s",
            text="n/a",
            original_text="n/a",
        ),
        Unit(
            unit_id="U4",
            record_id="R2",
            source="s",
            text="email user@example.com",
            original_text="email user@example.com",
        ),
        Unit(
            unit_id="U5",
            record_id="R3",
            source="s",
            text="This is helpful but hard.",
            original_text="This is helpful but hard.",
        ),
        Unit(
            unit_id="U6",
            record_id="R3",
            source="s",
            text="This is helpful but hard.",
            original_text="This is helpful but hard.",
        ),
    ]
    updated, report = assess_quality(units)
    assert updated[0].quality_flags == ["empty"]
    assert "too_short" in updated[1].quality_flags
    assert "low_effort" in updated[2].quality_flags
    assert "pii" in updated[3].quality_flags
    assert "duplicate" in updated[5].quality_flags
    assert report.flagged_units >= 4
    assert any(summary.severity == "exclude" for summary in report.summaries)

    assert parse_str_list([" a ", "", "b"]) == ["a", "b"]
    assert parse_str_list("a, b ,, c") == ["a", "b", "c"]
    assert parse_str_list(None) == []

    rows, cooccurrence = compute_quantification(
        _units(),
        _assignments()
        + [
            CodeAssignment(
                unit_id="missing",
                assignments=[CodeAssignmentEntry(code_id="C1", evidence="x")],
            )
        ],
        _codebook(),
    )
    assert rows[0].mentions >= 1
    assert cooccurrence[0].respondents == 1

    variables = plan_segments(_units(), overrides=["plan"], max_values=5)
    assert variables[0].name == "plan"
    assert variables[0].source == "override"
    breakdowns = compute_segment_breakdowns(
        variables,
        _units(),
        _assignments(),
        _codebook(),
        min_sample_size=2,
    )
    assert any(row.sample_size_guard == "small_n" for row in breakdowns)
    comparisons = compare_segments(
        [
            SegmentBreakdownRow(
                segment="plan",
                value="pro",
                theme_id="T1",
                respondents=5,
                total_respondents=5,
                pct_respondents=100.0,
                sample_size_guard="ok",
            ),
            SegmentBreakdownRow(
                segment="plan",
                value="basic",
                theme_id="T1",
                respondents=1,
                total_respondents=5,
                pct_respondents=20.0,
                sample_size_guard="ok",
            ),
        ]
    )
    assert comparisons[0].signal == "strong"


def test_report_helpers_render_and_validate() -> None:
    data = _report_state()
    errors = validate_final_state(data)
    assert errors == []

    invalid = ReportData(
        approved_codebook=None,
        units=_units(),
        code_assignments_pass2=[
            CodeAssignment(
                unit_id="U0001",
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
        approved_insight_ids=["missing"],
    )
    assert validate_final_state(invalid)[0] == "Missing codebook."

    report = render_markdown_report(data)
    assert "# Theme Reporter" in report
    assert "## Recommendations" in report
    assert "## Evidence Index" in report


def test_source_parser_and_normalisation_helpers_cover_branch_variants(
    tmp_path,
) -> None:
    assert pick_text_field(["id", "response"]) == "response"
    assert pick_text_field(["id", "other"]) == "other"
    assert pick_id_field(["respondent_id", "text"]) == "respondent_id"
    assert pick_id_field(["text", "body"]) is None

    assert SourceParser.sniff_type("support.csv", "") == "support_tickets"
    assert SourceParser.sniff_type("chat.log", "") == "chat_log"
    assert SourceParser.sniff_type("survey.csv", "") == "survey_csv"
    assert SourceParser.sniff_type("transcript.json", "") == "transcript"
    assert SourceParser.sniff_type(None, '{"messages": []}') == "chat_log"
    assert SourceParser.sniff_type(None, '{"ticket": true}') == "support_tickets"
    assert SourceParser.sniff_type(None, "id,text\n1,hello") == "survey_csv"
    assert SourceParser.sniff_type(None, "plain text") == "transcript"

    survey_records = SourceParser.parse_survey_csv("id,text\n1,Hello\n")
    assert survey_records[0] == ParsedRecord(
        record_id="1",
        source="survey:text",
        speaker=None,
        text="Hello",
        metadata={},
    )
    assert (
        SourceParser.parse_survey_csv("id,response\n1,Hello\n", flexible_columns=False)
        == []
    )
    assert (
        SourceParser.parse_survey_csv("id,response\n1,Hello\n", flexible_columns=True)[
            0
        ].text
        == "Hello"
    )

    assert (
        SourceParser.parse_transcript_json(
            json.dumps(
                [
                    {"speaker": "A", "text": "Hello", "other": 1},
                    "bad",
                    {"text": ""},
                ]
            )
        )[0].speaker
        == "A"
    )
    assert (
        SourceParser.parse_transcript_plain("A: Hello\nPlain line\n")[-1].speaker
        is None
    )
    assert SourceParser.parse_transcript("A: Hello\n")[0].source == "transcript:A"
    assert SourceParser.parse_chat_log(
        "not-json\n"
    ) == SourceParser.parse_transcript_plain("not-json\n")

    assert (
        SourceParser.parse_support_tickets(
            json.dumps({"tickets": [{"requester": "a", "text": "Help"}]})
        )[0].source
        == "support_ticket"
    )
    assert SourceParser.parse_support_tickets(
        "id,text\n1,Help\n", filename="tickets.csv"
    )[0].source.startswith("support_ticket:")

    payload = {"content": "  hello  "}
    assert SourceParser.load_payload_content(payload) == "  hello  "
    assert (
        SourceParser.load_payload_content(
            {"storage_path": str(tmp_path / "secret.txt")}
        )
        == ""
    )

    records, source_type = SourceParser.parse_payload(
        {"content": "id,text\n1,Hello\n", "source_type": "survey_csv"},
        flexible_columns=False,
    )
    assert source_type == "survey_csv"
    assert records[0].text == "Hello"
    records, source_type = SourceParser.parse_payload(
        {"content": "ticket_id,subject\n1,Help\n", "filename": "tickets.csv"},
        allow_additional_sources=False,
    )
    assert records == []
    assert source_type == "support_tickets"
    assert SourceParser.normalise_payload(
        State({"inputs": {"documents": [{"content": "hello", "filename": "x.csv"}]}})
    ) == {
        "source_type": None,
        "content": "hello",
        "storage_path": None,
        "filename": "x.csv",
    }
    assert SourceParser.normalise_payload(
        State({}),
        {
            "configurable": {
                "source": "hello",
                "source_type": "transcript",
                "source_filename": "x.txt",
            }
        },
    ) == {
        "source_type": "transcript",
        "content": "hello",
        "storage_path": None,
        "filename": "x.txt",
    }
