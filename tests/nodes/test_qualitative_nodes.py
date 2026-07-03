from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.logic import StructuredRouterDispatchNode
from orcheo.nodes.qualitative import (
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    CodebookOutputNode,
    CodedDataIngestNode,
    DataQualityNode,
    IngestNode,
    LLMStageFinalizeNode,
    LoadAttachmentNode,
    QualitativeResultKeys,
    RecodingBatchResponse,
    SetupNode,
    Subtheme,
    Theme,
    Unit,
    ValidateFilesNode,
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
async def test_load_attachment_node_loads_inline_documents() -> None:
    node = LoadAttachmentNode(name="load_attachments")
    state = State(
        {
            "inputs": {
                "documents": [
                    {
                        "filename": "survey.csv",
                        "content": "id,text\n1,Clear setup.\n",
                        "content_type": "text/csv",
                    }
                ]
            }
        }
    )

    result = await node(state, RunnableConfig())

    attachments = result["results"]["load_attachments"]["attachments"]
    assert attachments == [
        {
            "filename": "survey.csv",
            "content": "id,text\n1,Clear setup.\n",
            "content_type": "text/csv",
            "source_type": None,
            "source": "input",
            "attachment_id": None,
            "storage_path": None,
            "errors": [],
        }
    ]


@pytest.mark.asyncio
async def test_validate_files_node_accepts_raw_data_and_codebook() -> None:
    load_result = await LoadAttachmentNode(name="load_attachments")(
        State(
            {
                "inputs": {
                    "documents": [
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Clear setup.\n",
                        },
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Onboarding,C1,Clear setup,Easy setup\n"
                            ),
                        },
                    ]
                }
            }
        ),
        RunnableConfig(),
    )
    node = ValidateFilesNode(name="validate_files")

    result = await node(State(load_result), RunnableConfig())

    validated = result["results"]["validate_files"]
    assert "assistant_message" not in result
    assert validated["assistant_message"] == (
        "Files look valid: found data file `survey.csv` "
        "(1 record(s), survey_csv) and codebook `codebook.csv`."
    )
    assert validated["ok"] is True
    assert validated["errors"] == []
    assert validated["data_file"]["filename"] == "survey.csv"
    assert validated["data_file"]["kind"] == "raw"
    assert validated["data_file"]["source_type"] == "survey_csv"
    assert "content" not in validated["data_file"]
    assert validated["data_file"]["record_count"] == 1
    assert "codebook" not in validated
    assert validated["codebook_file"] == {
        "filename": "codebook.csv",
        "present": True,
    }


@pytest.mark.asyncio
async def test_validate_files_node_rejects_coded_data_when_raw_expected() -> None:
    codebook = _simple_codebook()
    csv_text, _ = build_coded_data_csv(
        [
            Unit(
                unit_id="U0001",
                record_id="R1",
                source="survey",
                text="Clear setup.",
                original_text="Clear setup.",
            )
        ],
        [
            CodeAssignment(
                unit_id="U0001",
                assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
            )
        ],
        codebook,
    )
    state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
                        {
                            "filename": "coded_data.csv",
                            "content": csv_text,
                            "errors": [],
                        }
                    ]
                }
            }
        }
    )

    result = await ValidateFilesNode(name="validate_files")(state, RunnableConfig())

    validated = result["results"]["validate_files"]
    assert "assistant_message" not in result
    assert validated["assistant_message"].startswith("File validation failed.")
    assert "raw data is expected" in validated["assistant_message"]
    assert validated["ok"] is False
    assert "No valid data file found." in validated["errors"]
    assert any("raw data is expected" in error for error in validated["errors"])


@pytest.mark.asyncio
async def test_validate_files_node_ignores_legacy_data_field_override() -> None:
    state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Clear setup.\n",
                            "errors": [],
                        }
                    ]
                }
            }
        }
    )
    node = ValidateFilesNode(name="validate_files", data_field="source_payload")

    result = await node(state, RunnableConfig())

    validated = result["results"]["validate_files"]
    assert "source_payload" not in validated
    assert validated["data_file"]["filename"] == "survey.csv"
    assert "Files look valid" in validated["assistant_message"]


@pytest.mark.asyncio
async def test_validate_files_node_accepts_coded_data() -> None:
    csv_text, _ = build_coded_data_csv(
        [
            Unit(
                unit_id="U0001",
                record_id="R1",
                source="survey",
                text="Clear setup.",
                original_text="Clear setup.",
            )
        ],
        [
            CodeAssignment(
                unit_id="U0001",
                assignments=[CodeAssignmentEntry(code_id="C1", confidence=0.9)],
            )
        ],
        _simple_codebook(),
    )
    state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
                        {
                            "filename": "coded_data.csv",
                            "content": csv_text,
                            "errors": [],
                        }
                    ]
                }
            }
        }
    )
    node = ValidateFilesNode(name="validate_files", data_kind="coded")

    result = await node(state, RunnableConfig())

    validated = result["results"]["validate_files"]
    assert validated["assistant_message"] == (
        "Files look valid: found coded data file `coded_data.csv` "
        "(1 unit(s), 1 assignment(s))."
    )
    assert validated["ok"] is True
    assert validated["errors"] == []
    assert validated["data_file"]["filename"] == "coded_data.csv"
    assert validated["data_file"]["kind"] == "coded"
    assert "content" not in validated["data_file"]
    assert validated["data_file"]["unit_count"] == 1
    assert validated["data_file"]["assignment_count"] == 1


@pytest.mark.asyncio
async def test_setup_node_persists_objective_and_source_payload() -> None:
    node = SetupNode(name="setup")
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
    node = SetupNode(
        name="setup",
        resolve_seed_codebook=True,
        exclude_codebook_docs=True,
    )
    state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
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
    node = IngestNode(name="ingest")
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
async def test_router_dispatch_node() -> None:
    router = StructuredRouterDispatchNode(
        name="router_dispatch",
        carried_fields=["research_objective"],
        assistant_message_fallback="fallback",
    )
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


@pytest.mark.asyncio
async def test_codebook_output_renders_markdown_table() -> None:
    node = CodebookOutputNode(
        name="codebook_output",
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
async def test_open_coder_finalize_prefers_structured_response_dict() -> None:
    node = LLMStageFinalizeNode(name="open_coder_finalize", stage="open_coder")
    unit = Unit(
        unit_id="u1",
        record_id="r1",
        source="survey.csv",
        text="The setup was clear.",
        original_text="The setup was clear.",
    )
    state = State(
        {
            "results": {
                "ingest": {"units": [unit.model_dump(mode="json")]},
                "open_coder_prepare": {
                    "batch_index": 0,
                    "total_batches": 1,
                    "batch_size": 25,
                },
            },
            "structured_response": {
                "assignments": [
                    {
                        "unit_id": "u1",
                        "assignments": [
                            {
                                "code_id": "clear setup",
                                "evidence": "setup was clear",
                                "confidence": 0.9,
                            }
                        ],
                    }
                ]
            },
            "messages": [AIMessage(content="unstructured fallback")],
        }
    )

    result = await node(state, RunnableConfig())

    assignments = result["results"]["open_coder_finalize"]["code_assignments_pass1"]
    assert assignments[0]["unit_id"] == "u1"
    assert assignments[0]["assignments"][0]["code_id"] == "clear setup"


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
