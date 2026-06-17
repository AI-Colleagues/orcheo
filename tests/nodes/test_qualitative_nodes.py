from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.logic import FinalReplyNode, StructuredRouterDispatchNode
from orcheo.nodes.qualitative import (
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    CodebookOutputNode,
    CodedDataIngestNode,
    DataQualityNode,
    FileValidatorNode,
    IngestNode,
    LLMStageFinalizeNode,
    QualitativeResultKeys,
    RecodingBatchResponse,
    SetupNode,
    Subtheme,
    Theme,
    Unit,
    build_coded_data_csv,
    merge_codebooks,
    parse_coded_data_csv,
)
from orcheo.nodes.qualitative.sources import SourceParser


def _simple_codebook() -> Codebook:
    return Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Onboarding",
                subthemes=[
                    Subtheme(code_id="C1", title="Clear setup", definition="Easy."),
                    Subtheme(code_id="C2", title="Hard auth", definition="Confusing."),
                ],
            )
        ]
    )


# Result-key wiring mirroring the recoding / reporting colleague workflows.
_RECODE_KEYS = QualitativeResultKeys(
    assignments_field="assignments",
    assignments_producers=("recoder_finalize",),
    approved_codebook_producers=("setup",),
)
_REPORT_KEYS = QualitativeResultKeys(
    assignments_field="code_assignments",
    assignments_producers=("ingest",),
    approved_codebook_producers=("ingest", "setup"),
    source_payload_producers=("setup", "ingest"),
)
_CHAINED_REPORT_KEYS = QualitativeResultKeys(
    assignments_field="code_assignments_pass2",
    assignments_producers=("ingest", "recoder_finalize"),
    approved_codebook_producers=("ingest", "setup"),
    source_payload_producers=("setup", "ingest"),
    units_producers=("ingest", "data_quality"),
)


@pytest.mark.asyncio
async def test_setup_node_persists_objective_and_source_payload() -> None:
    node = SetupNode(name="setup", result_keys=QualitativeResultKeys())
    state = State(
        {
            "inputs": {
                "research_objective": "Understand onboarding pain points",
                "documents": [
                    {
                        "filename": "survey.csv",
                        "source_type": "survey_csv",
                        "content": "id,text\n1,The setup was clear.\n",
                    }
                ],
            }
        }
    )

    result = await node(state, RunnableConfig())

    setup_result = result["results"]["setup"]
    assert setup_result["research_objective"] == "Understand onboarding pain points"
    assert setup_result["source_payload"]["filename"] == "survey.csv"


@pytest.mark.asyncio
async def test_setup_node_resolves_seed_codebook_without_using_it_as_source() -> None:
    keys = QualitativeResultKeys()
    node = SetupNode(
        name="setup",
        result_keys=keys,
        resolve_seed_codebook=True,
        exclude_codebook_docs=True,
    )
    state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title\n"
                                "T1,Onboarding,C1,Clear setup\n"
                            ),
                        },
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,The setup was clear.\n",
                        },
                    ]
                }
            }
        }
    )

    result = await node(state, RunnableConfig())

    setup_result = result["results"]["setup"]
    assert setup_result["source_payload"]["filename"] == "survey.csv"
    assert setup_result["seed_codebook_from_file"]["themes"][0]["theme_id"] == "T1"


@pytest.mark.asyncio
async def test_ingest_node_parses_survey_rows() -> None:
    node = IngestNode(name="ingest", result_keys=QualitativeResultKeys())
    state = State(
        {
            "results": {
                "setup": {
                    "source_payload": {
                        "filename": "survey.csv",
                        "source_type": "survey_csv",
                        "content": "id,text\n1,The setup was clear.\n",
                    }
                }
            }
        }
    )

    result = await node(state, RunnableConfig())

    ingest_result = result["results"]["ingest"]
    assert ingest_result["unit_count"] == 1
    assert ingest_result["units"][0]["text"] == "The setup was clear."


@pytest.mark.asyncio
async def test_router_dispatch_and_final_reply_nodes() -> None:
    router = StructuredRouterDispatchNode(
        name="router_dispatch",
        carried_fields=["research_objective"],
        assistant_message_fallback="fallback",
    )
    reply = FinalReplyNode(name="final_reply", fallback_message="fallback")

    routed_state = State(
        {
            "structured_response": {
                "action": "route",
                "branch": "generate_codebook",
                "research_objective": "Understand trust",
            }
        }
    )
    routed = await router(routed_state, RunnableConfig())
    assert routed["results"]["router_dispatch"]["routing"] == "generate_codebook"
    assert (
        routed["results"]["router_dispatch"]["research_objective"] == "Understand trust"
    )

    reply_state = State({})
    final = await reply(reply_state, RunnableConfig())
    assert final["assistant_message"] == "fallback"
    assert final["messages"][0]["content"] == "fallback"


@pytest.mark.asyncio
async def test_codebook_output_renders_markdown_table() -> None:
    node = CodebookOutputNode(
        name="codebook_output",
        result_keys=QualitativeResultKeys(),
        title="Theme Analyst",
    )
    codebook = Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Onboarding",
                subthemes=[
                    Subtheme(
                        code_id="C1",
                        title="Clear setup",
                        definition="Setup is easy to follow.",
                        include=["mentions clear setup"],
                        exclude=["mentions broken auth"],
                    )
                ],
            )
        ]
    )
    state = State(
        {
            "results": {
                "ingest": {},
                "setup": {"research_objective": "Understand onboarding"},
                "codebook_consolidator_finalize": {
                    "draft_codebook": codebook.model_dump(mode="json")
                },
            }
        }
    )

    result = await node(state, RunnableConfig())

    message = result["assistant_message"]
    assert "# Theme Analyst - Draft Codebook" in message
    assert "| Theme ID | Theme Title |" in message
    assert "Understand onboarding" in message


@pytest.mark.asyncio
async def test_data_quality_node_flags_and_reports() -> None:
    node = DataQualityNode(name="data_quality", result_keys=QualitativeResultKeys())
    units = [
        Unit(
            unit_id="U0001", record_id="R1", source="s", text="n/a", original_text="n/a"
        ),
        Unit(
            unit_id="U0002",
            record_id="R2",
            source="s",
            text="The onboarding flow was clear and helpful.",
            original_text="The onboarding flow was clear and helpful.",
        ),
        Unit(
            unit_id="U0003",
            record_id="R3",
            source="s",
            text="The onboarding flow was clear and helpful.",
            original_text="The onboarding flow was clear and helpful.",
        ),
    ]
    state = State(
        {"results": {"ingest": {"units": [u.model_dump(mode="json") for u in units]}}}
    )

    result = (await node(state, RunnableConfig()))["results"]["data_quality"]

    assert result["quality_report"]["total_units"] == 3
    assert "low_effort" in result["quality_report"]["unit_flags"]["U0001"]
    assert "duplicate" in result["quality_report"]["unit_flags"]["U0003"]
    assert result["flagged_units"] >= 2


def test_source_parser_rejects_raw_storage_paths(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("do not ingest", encoding="utf-8")

    assert SourceParser.load_payload_content({"storage_path": str(secret_file)}) == ""


def test_parse_coded_data_csv_requires_coded_headers() -> None:
    csv_text = "unit_id,text\nU0001,plain survey response\n"

    assert parse_coded_data_csv(csv_text) is None


def test_merge_codebooks_remints_duplicate_emergent_code_ids() -> None:
    seed = Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Seed",
                subthemes=[Subtheme(code_id="C001", title="Seed code")],
            )
        ]
    )
    emergent = Codebook(
        themes=[
            Theme(
                theme_id="T2",
                title="Emergent",
                subthemes=[Subtheme(code_id="C001", title="New code")],
            )
        ]
    )

    merged = merge_codebooks(seed, emergent)
    code_ids = [
        subtheme.code_id for theme in merged.themes for subtheme in theme.subthemes
    ]

    assert len(code_ids) == len(set(code_ids))
    assert code_ids == ["C001", "C002"]


def test_coded_data_csv_round_trips() -> None:
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="survey:text",
            text="Setup was clear",
            original_text="Setup was clear",
            metadata={"plan": "pro"},
        )
    ]
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[
                CodeAssignmentEntry(
                    code_id="C1", evidence="clear", confidence=0.9, sentiment="positive"
                )
            ],
        )
    ]

    csv_text, total = build_coded_data_csv(units, assignments, codebook)
    assert total == 1

    parsed = parse_coded_data_csv(csv_text)
    assert parsed is not None
    parsed_units, parsed_assignments, parsed_codebook = parsed
    assert parsed_units[0].unit_id == "U0001"
    assert parsed_units[0].metadata == {"plan": "pro"}
    assert parsed_assignments[0].assignments[0].code_id == "C1"
    assert parsed_codebook is not None


@pytest.mark.asyncio
async def test_coded_data_ingest_node_quantifies() -> None:
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear",
            original_text="clear",
        ),
        Unit(
            unit_id="U0002",
            record_id="R2",
            source="s",
            text="hard",
            original_text="hard",
        ),
    ]
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
        ),
        CodeAssignment(
            unit_id="U0002",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.8)],
        ),
    ]
    csv_text, _ = build_coded_data_csv(units, assignments, codebook)

    node = CodedDataIngestNode(name="ingest", result_keys=_REPORT_KEYS)
    state = State({"results": {"setup": {"source_payload": {"content": csv_text}}}})

    result = (await node(state, RunnableConfig()))["results"]["ingest"]

    assert result["unit_count"] == 2
    assert result["assignment_count"] == 2
    quant = {row["theme_id"]: row for row in result["quantification"]}
    assert quant["T1"]["respondents"] == 2


@pytest.mark.asyncio
async def test_coded_data_ingest_node_halts_without_assignments() -> None:
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear",
            original_text="clear",
        )
    ]
    csv_text, total = build_coded_data_csv(units, [], codebook)
    node = CodedDataIngestNode(name="ingest", result_keys=_REPORT_KEYS)
    state = State({"results": {"setup": {"source_payload": {"content": csv_text}}}})

    result = (await node(state, RunnableConfig()))["results"]["ingest"]

    assert total == 0
    assert result["halt"] is True
    assert result["assistant_message"] == node.missing_assignments_message


@pytest.mark.asyncio
async def test_coded_data_ingest_node_quantifies_chained_results() -> None:
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear",
            original_text="clear",
        ),
        Unit(
            unit_id="U0002",
            record_id="R2",
            source="s",
            text="hard",
            original_text="hard",
        ),
    ]
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
        ),
        CodeAssignment(
            unit_id="U0002",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.8)],
        ),
    ]
    node = CodedDataIngestNode(
        name="ingest",
        result_keys=_CHAINED_REPORT_KEYS,
        allow_chained_results=True,
    )
    state = State(
        {
            "results": {
                "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                "data_quality": {"units": [u.model_dump(mode="json") for u in units]},
                "recoder_finalize": {
                    "code_assignments_pass2": [
                        a.model_dump(mode="json") for a in assignments
                    ]
                },
            }
        }
    )

    result = (await node(state, RunnableConfig()))["results"]["ingest"]

    assert result["unit_count"] == 2
    assert result["assignment_count"] == 2
    quant = {row["theme_id"]: row for row in result["quantification"]}
    assert quant["T1"]["respondents"] == 2


@pytest.mark.asyncio
async def test_recoder_finalize_merges_assignments() -> None:
    node = LLMStageFinalizeNode(
        name="recoder_finalize",
        stage="recoder",
        result_keys=_RECODE_KEYS,
        response_schema=RecodingBatchResponse,
    )
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear setup",
            original_text="clear setup",
        ),
    ]
    structured = RecodingBatchResponse(
        assignments=[
            CodeAssignment(
                unit_id="U0001",
                assignments=[
                    CodeAssignmentEntry(code_id="C1", evidence="clear", confidence=0.9),
                    CodeAssignmentEntry(code_id="ZZZ", evidence="x", confidence=0.5),
                ],
            )
        ]
    )
    state = State(
        {
            "structured_response": structured,
            "results": {
                "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                "recoder_prepare": {
                    "batch_index": 0,
                    "batch_end_index": 1,
                    "total_batches": 1,
                    "batch_size": 25,
                },
            },
        }
    )

    result = (await node(state, RunnableConfig()))["results"]["recoder_finalize"]

    assert result["continue_llm"] is False
    assert result["done"] is True
    saved = result["assignments"][0]["assignments"]
    # Invented code "ZZZ" is filtered; only the valid C1 survives.
    assert [e["code_id"] for e in saved] == ["C1"]


@pytest.mark.asyncio
async def test_file_validator_recognises_coded_data() -> None:
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear",
            original_text="clear",
        ),
    ]
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
        )
    ]
    csv_text, _ = build_coded_data_csv(units, assignments, codebook)

    node = FileValidatorNode(
        name="validate_files",
        result_keys=_REPORT_KEYS,
        data_file_kind="coded",
        single_data_file=True,
        codebook_result_field=_REPORT_KEYS.approved_codebook_field,
        announce_seed_codebook=False,
        missing_data_message="No coded data CSV was found.",
        ready_message="Ready",
    )
    state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"content": csv_text, "filename": "coded_data.csv"}
                    ]
                }
            }
        }
    )

    result = await node(state, RunnableConfig())

    assert "coded data" in result["assistant_message"]
    assert "Ready" in result["assistant_message"]


@pytest.mark.asyncio
async def test_file_validator_auto_classifies_raw_codebook_and_coded_data() -> None:
    keys = QualitativeResultKeys(source_payload_field="source_payload")
    codebook = _simple_codebook()
    units = [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="s",
            text="clear",
            original_text="clear",
        ),
    ]
    assignments = [
        CodeAssignment(
            unit_id="U0001",
            assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
        )
    ]
    coded_csv, _ = build_coded_data_csv(units, assignments, codebook)
    node = FileValidatorNode(
        name="validate_files",
        result_keys=keys,
        data_file_kind="auto",
        codebook_result_field="approved_codebook",
        seed_codebook_result_field="seed_codebook_from_file",
        coded_data_result_field="coded_data_payload",
        ready_message="Ready",
    )
    state = State(
        {
            "results": {
                "context_pre": {
                    "pending_documents": [
                        {"filename": "survey.csv", "content": "id,text\n1,Clear.\n"},
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title\n"
                                "T1,Onboarding,C1,Clear setup\n"
                            ),
                        },
                        {"filename": "coded_data.csv", "content": coded_csv},
                    ]
                }
            }
        }
    )

    result = await node(state, RunnableConfig())
    validate_result = result["results"]["validate_files"]

    assert "survey.csv" in result["assistant_message"]
    assert "codebook.csv" in result["assistant_message"]
    assert "coded_data.csv" in result["assistant_message"]
    assert validate_result["source_payload"]["filename"] == "survey.csv"
    assert validate_result["coded_data_payload"]["filename"] == "coded_data.csv"
    assert validate_result["approved_codebook"]["themes"][0]["theme_id"] == "T1"
    assert validate_result["seed_codebook_from_file"]["themes"][0]["theme_id"] == "T1"
