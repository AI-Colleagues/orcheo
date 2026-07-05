from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.qualitative.codebook import get_seed_codebook, parse_codebook_csv
from orcheo.nodes.qualitative.coded_data import build_coded_data_csv
from orcheo.nodes.qualitative.insights import (
    InsightCriticNode,
    RecommendationGeneratorNode,
)
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import (
    CandidateInsight,
    CodeAssignment,
    CodeAssignmentEntry,
    Codebook,
    Recommendation,
    ReportData,
    Subtheme,
    Theme,
    Unit,
)
from orcheo.nodes.qualitative.pipeline import (
    CodebookOutputNode,
    ExportCodebookNode,
    ExportCodedDataNode,
    IngestNode,
    LoadAttachmentsNode,
    RecodeOutputNode,
    ValidateFilesNode,
)
from orcheo.nodes.qualitative.quality import DataQualityNode
from orcheo.nodes.qualitative.quantify import CodedDataIngestNode
from orcheo.nodes.qualitative.report import ExportReportNode, ReportOutputNode
from orcheo.nodes.qualitative.stages import (
    LLMStageFinalizeNode,
    LLMStagePrepareNode,
)


def _codebook() -> Codebook:
    return Codebook(
        themes=[
            Theme(
                theme_id="T1",
                title="Onboarding",
                subthemes=[
                    Subtheme(code_id="C1", title="Clear setup", definition="Easy"),
                    Subtheme(code_id="C2", title="Hard auth", definition="Confusing"),
                ],
            )
        ]
    )


def _units() -> list[Unit]:
    return [
        Unit(
            unit_id="U0001",
            record_id="R1",
            source="survey",
            text="The setup was clear.",
            original_text="The setup was clear.",
            metadata={"plan": "pro"},
        ),
        Unit(
            unit_id="U0002",
            record_id="R2",
            source="survey",
            speaker="Ana",
            text="The login was confusing but helpful.",
            original_text="The login was confusing but helpful.",
            metadata={"plan": "basic"},
        ),
    ]


def _assignments() -> list[CodeAssignment]:
    return [
        CodeAssignment(
            unit_id="U0001",
            assignments=[
                CodeAssignmentEntry(code_id="C1", evidence="clear", confidence=0.9),
            ],
        ),
        CodeAssignment(
            unit_id="U0002",
            assignments=[
                CodeAssignmentEntry(code_id="C2", evidence="confusing", confidence=0.7),
            ],
        ),
    ]


def _report_state() -> ReportData:
    return ReportData(
        research_objective="Understand onboarding",
        approved_codebook=_codebook(),
        units=_units(),
        code_assignments_pass2=_assignments(),
        quantification=[],
        selected_quotes=[],
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding is clear",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
                evidence_strength="high",
            )
        ],
        approved_insight_ids=["I1"],
    )


@pytest.mark.asyncio
async def test_context_load_ingest_and_quality_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return _Attachment(b"caf\xe9", "attached.txt")

    load_attachments = LoadAttachmentsNode(name="load_attachments")
    resolved = (
        await load_attachments(
            State(
                {
                    "inputs": {
                        "documents": [
                            {"attachment_id": "att-1", "filename": ""},
                            {
                                "storage_path": str(tmp_path / "inline.txt"),
                                "filename": "inline.txt",
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
    )["results"]["load_attachments"]
    assert resolved["attachments"][0]["filename"] == "attached.txt"
    assert resolved["attachments"][0]["content"] == "café"

    ingest = IngestNode(name="ingest", require_codebook=True)
    halted = (await ingest(State({"results": {}}), {}))["results"]["ingest"]
    assert halted["halt"] is True

    ingest_ok = IngestNode(name="ingest")
    ingest_state = State(
        {
            "results": {
                "setup": {
                    "source_payload": {
                        "filename": "survey.csv",
                        "content": "id,text\n1,Hello there world\n",
                        "source_type": "survey_csv",
                    }
                }
            }
        }
    )
    ingested = (await ingest_ok(ingest_state, {}))["results"]["ingest"]
    assert ingested["unit_count"] == 1
    assert ingested["units"][0]["unit_id"] == "U0001"

    quality = DataQualityNode(name="data_quality")
    quality_result = (await quality(State({"results": {"ingest": ingested}}), {}))[
        "results"
    ]["data_quality"]
    assert quality_result["flagged_units"] == 0
    assert quality_result["quality_report"]["total_units"] == 1


@pytest.mark.asyncio
async def test_ingest_node_uses_loaded_attachments_without_setup() -> None:
    node = IngestNode(
        name="ingest",
        pending_documents="{{results.load_attachments.attachments}}",
    )
    state = State(
        {
            "inputs": {
                "documents": [
                    {
                        "filename": "codebook.csv",
                        "content": (
                            "theme_id,theme_title,code_id,code_title\nT1,A,C1,B\n"
                        ),
                    }
                ]
            },
            "results": {
                "load_attachments": {
                    "attachments": [
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title\nT1,A,C1,B\n"
                            ),
                        },
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Loaded attachment text\n",
                        },
                    ]
                }
            },
        }
    )

    result = (await node(state, {}))["results"]["ingest"]

    assert result["halt"] is False
    assert result["source_payload"]["filename"] == "survey.csv"
    assert result["unit_count"] == 1
    assert result["units"][0]["text"] == "Loaded attachment text"


def test_get_seed_codebook_uses_loaded_attachments_without_setup() -> None:
    keys = QualitativeResultKeys(
        pending_documents_field="attachments",
        pending_documents_producers=("load_attachments",),
    )
    state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Loaded attachment text\n",
                        },
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title\n"
                                "T1,Onboarding,C1,Clear setup\n"
                            ),
                        },
                    ]
                }
            }
        }
    )

    codebook = get_seed_codebook({}, state, keys)

    assert codebook is not None
    assert codebook.themes[0].theme_id == "T1"


@pytest.mark.asyncio
async def test_validate_files_codebook_and_export_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    validator = ValidateFilesNode(
        name="validate_files",
        require_codebook=True,
    )
    no_files = await validator(State({"results": {}}), {})
    assert "assistant_message" not in no_files
    assert no_files["results"]["validate_files"]["assistant_message"].startswith(
        "File validation failed."
    )
    assert (
        "No attachments were loaded." in no_files["results"]["validate_files"]["errors"]
    )

    codebook = _codebook()
    validation_state = State(
        {
            "results": {
                "load_attachments": {
                    "attachments": [
                        {"filename": "survey.csv", "content": "id,text\n1,Hello\n"},
                        {
                            "filename": "codebook.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Onboarding,C1,Clear setup,Easy\n"
                            ),
                        },
                    ]
                }
            }
        }
    )
    validated_result = await validator(validation_state, {})
    validated = validated_result["results"]["validate_files"]
    assert "assistant_message" not in validated_result
    assert validated["assistant_message"] == (
        "Files look valid: found data file `survey.csv` "
        "(1 record(s), survey_csv) and codebook `codebook.csv`."
    )
    assert validated["ok"] is True
    assert validated["data_file"]["filename"] == "survey.csv"
    assert "content" not in validated["data_file"]
    assert "codebook" not in validated
    assert validated["codebook_file"] == {
        "filename": "codebook.csv",
        "present": True,
    }

    codebook_output = CodebookOutputNode(
        name="codebook_output",
        ingest_node_name="ingest",
        max_coding_batches=1,
        default_batch_size=1,
    )
    halted = await codebook_output(
        State({"results": {"ingest": {"halt": True, "assistant_message": "stop"}}}), {}
    )
    assert halted["assistant_message"] == "stop"
    rendered = await codebook_output(
        State(
            {
                "results": {
                    "ingest": {"halt": False},
                    "setup": {"research_objective": "Understand onboarding"},
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    },
                    "ingest": {"units": [u.model_dump(mode="json") for u in _units()]},
                }
            }
        ),
        {},
    )
    assert "Draft Codebook" in rendered["assistant_message"]
    assert "per-run limit" in rendered["assistant_message"]

    export_codebook = ExportCodebookNode(
        name="export_codebook", codebook="{{missing.codebook}}"
    )
    missing = await export_codebook(State({"messages": []}), {})
    assert missing["assistant_message"] == export_codebook.missing_codebook_message

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/codebook.csv")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment",
        _upload_ok,
    )
    export_codebook = ExportCodebookNode(
        name="export_codebook", codebook="{{results.reviewed.codebook}}"
    )
    exported = await export_codebook(
        State(
            {
                "results": {
                    "reviewed": {
                        "codebook": codebook.model_dump(mode="json"),
                    }
                }
            }
        ),
        {},
    )
    assert "Download codebook.csv" in exported["assistant_message"]
    assert "results" not in exported


@pytest.mark.asyncio
async def test_coded_data_ingest_and_recode_export_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    csv_text, _ = build_coded_data_csv(units, assignments, codebook)

    ingest = CodedDataIngestNode(name="ingest", allow_chained_results=False)
    missing = await ingest(State({"results": {"setup": {}}}), {})
    assert missing["results"]["ingest"]["halt"] is True

    chained = CodedDataIngestNode(name="ingest", allow_chained_results=True)
    chained_state = State(
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
    )
    chained_result = (await chained(chained_state, {}))["results"]["ingest"]
    assert chained_result["unit_count"] == 2

    recode = RecodeOutputNode(name="recode_data")
    early = await recode(
        State({"results": {"ingest": {"halt": True, "assistant_message": "halt"}}}),
        {},
    )
    assert early["assistant_message"] == "halt"

    no_assignments = await recode(State({"results": {"ingest": {}}}), {})
    assert "No code assignments" in no_assignments["assistant_message"]

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment",
        _upload_fail,
    )
    state = State(
        {
            "results": {
                "ingest": {"halt": False},
                "setup": {"approved_codebook": codebook.model_dump(mode="json")},
                "ingest": {
                    "units": [u.model_dump(mode="json") for u in units],
                },
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
    )
    failed = await recode(state, {"configurable": {"batch_size": 1}})
    assert "Could not generate the download link" in failed["assistant_message"]

    export_coded = ExportCodedDataNode(name="export_coded_data")
    missing_export = await export_coded(State({"results": {}}), {})
    assert missing_export["assistant_message"] == export_coded.missing_data_message


@pytest.mark.asyncio
async def test_stage_insight_and_report_nodes_cover_main_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    keys = QualitativeResultKeys()

    prepare = LLMStagePrepareNode(name="open_coder_prepare", stage="open_coder")
    assert (await prepare(State({"results": {}}), {}))["results"]["open_coder_prepare"][
        "skip_llm"
    ] is True
    open_state = State(
        {
            "results": {
                "setup": {keys.research_objective_field: "Objective"},
                "ingest": {
                    keys.units_field: [u.model_dump(mode="json") for u in units]
                },
            }
        }
    )
    open_prompt = (await prepare(open_state, {"configurable": {"batch_size": 1}}))[
        "results"
    ]["open_coder_prepare"]
    assert open_prompt["skip_llm"] is False
    assert "Units:" in open_prompt["input_text"]

    direct_prepare = LLMStagePrepareNode(
        name="open_coder_prepare",
        stage="open_coder",
        research_objective="{{results.router_dispatch.research_objective}}",
        units="{{results.ingest.units}}",
    )
    direct_prompt = (
        await direct_prepare(
            State(
                {
                    "results": {
                        "router_dispatch": {"research_objective": "Template objective"},
                        "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    }
                }
            ),
            {},
        )
    )["results"]["open_coder_prepare"]
    assert direct_prompt["objective"] == "Template objective"

    consolidator = LLMStagePrepareNode(
        name="codebook_consolidator_prepare", stage="codebook_consolidator"
    )
    no_assign = (await consolidator(State({"results": {}}), {}))["results"][
        "codebook_consolidator_prepare"
    ]
    assert no_assign["action"] == "no_assignments"

    recoder = LLMStagePrepareNode(name="recoder_prepare", stage="recoder")
    assert (await recoder(State({"results": {}}), {}))["results"]["recoder_prepare"][
        "skip_llm"
    ] is True

    quote_selector = LLMStagePrepareNode(
        name="quote_selector_prepare", stage="quote_selector"
    )
    assert (await quote_selector(State({"results": {}}), {}))["results"][
        "quote_selector_prepare"
    ]["skip_llm"] is True

    insight_generator = LLMStagePrepareNode(
        name="insight_generator_prepare", stage="insight_generator"
    )
    insight_prompt = (
        await insight_generator(
            State(
                {
                    "results": {
                        "setup": {keys.research_objective_field: "Objective"},
                        "setup_2": {},
                        "ingest": {
                            keys.approved_codebook_field: codebook.model_dump(
                                mode="json"
                            ),
                            keys.quantification_field: [],
                            keys.assignments_field: [
                                a.model_dump(mode="json") for a in assignments
                            ],
                            keys.selected_quotes_field: [],
                        },
                    }
                }
            ),
            {},
        )
    )["results"]["insight_generator_prepare"]
    assert insight_prompt["skip_llm"] is False

    finalize = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        response_schema=None,
    )
    assert (await finalize(State({"results": {"ingest": {}}}), {}))["results"][
        "open_coder_finalize"
    ]["next_index"] == 0

    direct_finalize = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        units="{{results.ingest.units}}",
        code_assignments="{{results.open_coder_finalize.code_assignments_pass1}}",
    )
    finalized = (
        await direct_finalize(
            State(
                {
                    "results": {
                        "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                        "open_coder_prepare": {
                            "batch_index": 0,
                            "total_batches": 1,
                            "batch_size": 10,
                        },
                    },
                    "structured_response": {
                        "assignments": [
                            {
                                "unit_id": "U0001",
                                "assignments": [{"code_id": "C1"}],
                            }
                        ]
                    },
                }
            ),
            {},
        )
    )["results"]["open_coder_finalize"]
    assert finalized["done"] is True
    assert finalized["code_assignments_pass1"][0]["unit_id"] == "U0001"

    output = CodebookOutputNode(
        name="codebook_output",
        codebook="{{results.codebook_consolidator_finalize.draft_codebook}}",
        research_objective="{{results.router_dispatch.research_objective}}",
        units="{{results.ingest.units}}",
    )
    rendered = await output(
        State(
            {
                "results": {
                    "router_dispatch": {"research_objective": "Template objective"},
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    },
                }
            }
        ),
        {},
    )
    assert "Template objective" in rendered["assistant_message"]

    critic = InsightCriticNode(name="insight_critic")
    critiqued = (
        await critic(
            State(
                {
                    "results": {
                        "setup": {
                            "approved_codebook": codebook.model_dump(mode="json")
                        },
                        "ingest": {
                            "units": [u.model_dump(mode="json") for u in units],
                            "segment_comparisons": [
                                {
                                    "segment": "plan",
                                    "theme_id": "T1",
                                    "high_value": "pro",
                                    "low_value": "basic",
                                    "high_pct": 90.0,
                                    "low_pct": 10.0,
                                    "delta_pct": 80.0,
                                    "signal": "weak",
                                    "note": "Weak contrast",
                                }
                            ],
                        },
                        "open_coder_finalize": {
                            "code_assignments_pass1": [
                                a.model_dump(mode="json") for a in assignments
                            ]
                        },
                        "recommendation_generator": {
                            "candidate_insights": [
                                {
                                    "insight_id": "I1",
                                    "observation": "Onboarding is clear",
                                    "supporting_codes": ["C1"],
                                    "supporting_units": ["U0001"],
                                    "evidence_strength": "high",
                                }
                            ]
                        },
                    }
                }
            ),
            {},
        )
    )["results"]["insight_critic"]
    assert critiqued["critiqued"] == 1

    recommender = RecommendationGeneratorNode(name="recommendation_generator")
    recommended = (
        await recommender(
            State(
                {
                    "results": {
                        "recommendation_generator": {
                            "candidate_insights": critiqued["candidate_insights"]
                        }
                    }
                }
            ),
            {},
        )
    )["results"]["recommendation_generator"]
    assert recommended["insights"] == 1
    assert recommended["approved_insight_ids"] == ["I1"]

    report_output = ReportOutputNode(name="report_output")
    early = await report_output(
        State({"results": {"ingest": {"halt": True, "assistant_message": "halt"}}}),
        {},
    )
    assert early["assistant_message"] == "halt"
    assert early["results"]["report_output"]["assistant_message"] == "halt"

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/report.md")

    monkeypatch.setattr("orcheo.nodes.qualitative.report.upload_attachment", _upload_ok)
    report_result = await report_output(
        State(
            {"results": {"ingest": {}, "setup": {"research_objective": "Objective"}}}
        ),
        {},
    )
    assert "Download insight_report.md" in report_result["assistant_message"]
    assert (
        report_result["results"]["report_output"]["assistant_message"]
        == report_result["assistant_message"]
    )

    export_report = ExportReportNode(name="export_report")
    missing = await export_report(State({"results": {}}), {})
    assert missing["assistant_message"] == export_report.missing_report_message
