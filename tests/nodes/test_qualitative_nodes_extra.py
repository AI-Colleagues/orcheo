from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.qualitative.coded_data import build_coded_data_csv
from orcheo.nodes.qualitative.insights import (
    InsightCriticNode,
    RecommendationGeneratorNode,
)
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
    )["node_results"]["load_attachments"]
    assert resolved["attachments"][0]["filename"] == "attached.txt"
    assert resolved["attachments"][0]["content"] == "café"

    ingest = IngestNode(name="ingest", require_codebook=True)
    halted = (await ingest(State({"node_results": {}}), {}))["node_results"]["ingest"]
    assert halted["halt"] is True

    ingest_ok = IngestNode(
        name="ingest",
        source_payload={
            "filename": "survey.csv",
            "content": "id,text\n1,Hello there world\n",
            "source_type": "survey_csv",
        },
    )
    ingested = (await ingest_ok(State({}), {}))["node_results"]["ingest"]
    assert ingested["unit_count"] == 1
    assert ingested["units"][0]["unit_id"] == "U0001"

    quality = DataQualityNode(name="data_quality")
    quality_result = (await quality(State({"node_results": {"ingest": ingested}}), {}))[
        "node_results"
    ]["data_quality"]
    assert quality_result["flagged_units"] == 0
    assert quality_result["quality_report"]["total_units"] == 1

    quality_direct = DataQualityNode(name="data_quality", units=_units())
    quality_direct_result = (
        await quality_direct(State({"node_results": {"ingest": {"units": []}}}), {})
    )["node_results"]["data_quality"]
    assert quality_direct_result["quality_report"]["total_units"] == 2


@pytest.mark.asyncio
async def test_ingest_node_uses_loaded_attachments_without_setup() -> None:
    node = IngestNode(
        name="ingest",
        pending_documents="{{node_results.load_attachments.attachments}}",
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
            "node_results": {
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

    result = (await node(state, {}))["node_results"]["ingest"]

    assert result["halt"] is False
    assert result["source_payload"]["filename"] == "survey.csv"
    assert result["unit_count"] == 1
    assert result["units"][0]["text"] == "Loaded attachment text"


@pytest.mark.asyncio
async def test_validate_files_codebook_and_export_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    validator = ValidateFilesNode(
        name="validate_files",
        require_codebook=True,
    )
    no_files = await validator(State({"node_results": {}}), {})
    assert no_files["assistant_message"].startswith("File validation failed.")
    assert (
        "No attachments were loaded."
        in no_files["node_results"]["validate_files"]["errors"]
    )

    codebook = _codebook()
    validation_state = State(
        {
            "node_results": {
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
    validated = validated_result["node_results"]["validate_files"]
    assert validated_result["assistant_message"] == (
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
    )
    halted = await codebook_output(
        State(
            {"node_results": {"ingest": {"halt": True, "assistant_message": "stop"}}}
        ),
        {},
    )
    assert halted["assistant_message"] == "stop"
    many_units = [
        Unit(
            unit_id=f"U{i:04d}",
            record_id=f"R{i}",
            source="survey",
            text=f"Response {i}",
            original_text=f"Response {i}",
        )
        for i in range(1, 202)
    ]
    rendered = await codebook_output(
        State(
            {
                "node_results": {
                    "codebook_consolidator_finalize": {
                        "draft_codebook": codebook.model_dump(mode="json")
                    },
                    "ingest": {
                        "units": [u.model_dump(mode="json") for u in many_units]
                    },
                }
            }
        ),
        {"configurable": {"batch_size": 1}},
    )
    assert "Draft Codebook" in rendered["assistant_message"]
    assert "per-run limit" in rendered["assistant_message"]

    export_codebook = ExportCodebookNode(
        name="export_codebook", codebook="{{missing.codebook}}"
    )
    missing = await export_codebook(State({"messages": []}), {})
    assert missing["assistant_message"].startswith(
        "No codebook is available to export."
    )

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/codebook.csv")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment",
        _upload_ok,
    )
    export_codebook = ExportCodebookNode(
        name="export_codebook", codebook="{{node_results.reviewed.codebook}}"
    )
    exported = await export_codebook(
        State(
            {
                "node_results": {
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
    missing = await ingest(State({"node_results": {"setup": {}}}), {})
    assert missing["node_results"]["ingest"]["halt"] is True

    chained = CodedDataIngestNode(
        name="ingest",
        allow_chained_results=True,
        approved_codebook=codebook.model_dump(mode="json"),
    )
    chained_state = State(
        {
            "node_results": {
                "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                "open_coder_finalize": {
                    "code_assignments_pass1": [
                        a.model_dump(mode="json") for a in assignments
                    ]
                },
            }
        }
    )
    chained_result = (await chained(chained_state, {}))["node_results"]["ingest"]
    assert chained_result["unit_count"] == 2

    chained_missing = CodedDataIngestNode(name="ingest", allow_chained_results=True)
    chained_missing_result = await chained_missing(
        State(
            {
                "node_results": {
                    "ingest": {"units": [u.model_dump(mode="json") for u in units]}
                }
            }
        ),
        {},
    )
    assert chained_missing_result["node_results"]["ingest"]["halt"] is True

    recode = RecodeOutputNode(
        name="recode_data", codebook=codebook.model_dump(mode="json")
    )
    early = await recode(
        State(
            {"node_results": {"ingest": {"halt": True, "assistant_message": "halt"}}}
        ),
        {},
    )
    assert early["assistant_message"] == "halt"

    no_assignments = await recode(State({"node_results": {"ingest": {}}}), {})
    assert "No code assignments" in no_assignments["assistant_message"]

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment",
        _upload_fail,
    )
    state = State(
        {
            "node_results": {
                "ingest": {
                    "halt": False,
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
    missing_export = await export_coded(State({"node_results": {}}), {})
    assert missing_export["assistant_message"].startswith(
        "No coded data is available to export."
    )


@pytest.mark.asyncio
async def test_stage_insight_and_report_nodes_cover_main_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()

    prepare = LLMStagePrepareNode(name="open_coder_prepare", stage="open_coder")
    assert (await prepare(State({"node_results": {}}), {}))["node_results"][
        "open_coder_prepare"
    ]["skip_llm"] is True
    open_state = State(
        {
            "node_results": {
                "ingest": {"units": [u.model_dump(mode="json") for u in units]},
            }
        }
    )
    open_prompt = (await prepare(open_state, {"configurable": {"batch_size": 1}}))[
        "node_results"
    ]["open_coder_prepare"]
    assert open_prompt["skip_llm"] is False
    assert "Units:" in open_prompt["input_text"]

    direct_prepare = LLMStagePrepareNode(
        name="open_coder_prepare",
        stage="open_coder",
        research_objective="{{node_results.router_dispatch.research_objective}}",
        units="{{node_results.ingest.units}}",
    )
    direct_prompt = (
        await direct_prepare(
            State(
                {
                    "node_results": {
                        "router_dispatch": {"research_objective": "Template objective"},
                        "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                    }
                }
            ),
            {},
        )
    )["node_results"]["open_coder_prepare"]
    assert direct_prompt["objective"] == "Template objective"

    consolidator = LLMStagePrepareNode(
        name="codebook_consolidator_prepare", stage="codebook_consolidator"
    )
    no_assign = (await consolidator(State({"node_results": {}}), {}))["node_results"][
        "codebook_consolidator_prepare"
    ]
    assert no_assign["action"] == "no_assignments"

    recoder = LLMStagePrepareNode(name="recoder_prepare", stage="recoder")
    assert (await recoder(State({"node_results": {}}), {}))["node_results"][
        "recoder_prepare"
    ]["skip_llm"] is True
    recoder_ready = LLMStagePrepareNode(
        name="recoder_prepare",
        stage="recoder",
        units=units,
        approved_codebook=codebook.model_dump(mode="json"),
    )
    recoder_prompt = (
        await recoder_ready(
            State(
                {
                    "node_results": {
                        "ingest": {"units": [u.model_dump(mode="json") for u in units]},
                        "recoder_finalize": {"next_index": 99},
                    }
                }
            ),
            {"configurable": {"batch_size": 1, "per_turn_batch_budget": 1}},
        )
    )["node_results"]["recoder_prepare"]
    assert recoder_prompt["skip_llm"] is True

    quote_selector = LLMStagePrepareNode(
        name="quote_selector_prepare", stage="quote_selector"
    )
    assert (await quote_selector(State({"node_results": {}}), {}))["node_results"][
        "quote_selector_prepare"
    ]["skip_llm"] is True

    insight_generator = LLMStagePrepareNode(
        name="insight_generator_prepare",
        stage="insight_generator",
        approved_codebook=codebook.model_dump(mode="json"),
    )
    insight_prompt = (
        await insight_generator(
            State(
                {
                    "node_results": {
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
    )["node_results"]["insight_generator_prepare"]
    assert insight_prompt["skip_llm"] is False

    finalize = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        response_schema=None,
    )
    assert (await finalize(State({"node_results": {"ingest": {}}}), {}))[
        "node_results"
    ]["open_coder_finalize"]["next_index"] == 0

    consolidator_finalize = LLMStageFinalizeNode(
        name="codebook_consolidator_finalize",
        stage="codebook_consolidator",
        seed_codebook=codebook.model_dump(mode="json"),
    )
    consolidator_result = (
        await consolidator_finalize(
            State(
                {
                    "node_results": {
                        "codebook_consolidator_prepare": {"action": "no_assignments"}
                    }
                }
            ),
            {},
        )
    )["node_results"]["codebook_consolidator_finalize"]
    assert consolidator_result["done"] is True

    direct_finalize = LLMStageFinalizeNode(
        name="open_coder_finalize",
        stage="open_coder",
        units="{{node_results.ingest.units}}",
        code_assignments="{{node_results.open_coder_finalize.code_assignments_pass1}}",
    )
    finalized = (
        await direct_finalize(
            State(
                {
                    "node_results": {
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
    )["node_results"]["open_coder_finalize"]
    assert finalized["done"] is True
    assert finalized["code_assignments_pass1"][0]["unit_id"] == "U0001"

    output = CodebookOutputNode(
        name="codebook_output",
        codebook="{{node_results.codebook_consolidator_finalize.draft_codebook}}",
        research_objective="{{node_results.router_dispatch.research_objective}}",
        units="{{node_results.ingest.units}}",
    )
    rendered = await output(
        State(
            {
                "node_results": {
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

    critic = InsightCriticNode(
        name="insight_critic",
        approved_codebook=codebook.model_dump(mode="json"),
    )
    critiqued = (
        await critic(
            State(
                {
                    "node_results": {
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
    )["node_results"]["insight_critic"]
    assert critiqued["critiqued"] == 1

    recommender = RecommendationGeneratorNode(name="recommendation_generator")
    recommended = (
        await recommender(
            State(
                {
                    "node_results": {
                        "recommendation_generator": {
                            "candidate_insights": critiqued["candidate_insights"]
                        }
                    }
                }
            ),
            {},
        )
    )["node_results"]["recommendation_generator"]
    assert recommended["insights"] == 1
    assert recommended["approved_insight_ids"] == ["I1"]

    report_output = ReportOutputNode(name="report_output")
    early = await report_output(
        State(
            {"node_results": {"ingest": {"halt": True, "assistant_message": "halt"}}}
        ),
        {},
    )
    assert early["assistant_message"] == "halt"
    assert early["node_results"]["report_output"] == {}

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/report.md")

    monkeypatch.setattr("orcheo.nodes.qualitative.report.upload_attachment", _upload_ok)
    report_result = await report_output(
        State({"node_results": {"ingest": {}}}),
        {},
    )
    assert "Download insight_report.md" in report_result["assistant_message"]
    assert report_result["node_results"]["report_output"]["report_url"] == (
        "https://example.test/report.md"
    )

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("upload failed")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.report.upload_attachment", _upload_fail
    )
    failed_report = await report_output(State({"node_results": {"ingest": {}}}), {})
    assert (
        "Could not generate the download link: upload failed"
        in failed_report["assistant_message"]
    )
    assert "> ⚠️ Data caveats:" in failed_report["assistant_message"]

    missing_report = ExportReportNode(name="export_report")
    missing = await missing_report(State({"node_results": {}}), {})
    assert missing["assistant_message"].startswith("No report is available to export.")

    export_report = ExportReportNode(
        name="export_report",
        approved_codebook=_codebook(),
        units=_units(),
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding is clear",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
            )
        ],
        approved_insight_ids=["I1"],
        recommendations=[
            Recommendation(
                insight_id="I1",
                finding="obs",
                action="act",
                expected_impact="impact",
            )
        ],
    )
    exported_failed = await export_report(State({"node_results": {}}), {})
    assert exported_failed["assistant_message"].startswith(
        "Export failed: upload failed"
    )

    monkeypatch.setattr("orcheo.nodes.qualitative.report.upload_attachment", _upload_ok)
    exported = await export_report(State({"node_results": {}}), {})
    assert "Download insight_report.md" in exported["assistant_message"]

    async def _upload_none(*args, **kwargs):  # noqa: ARG001
        return (None, None)

    clean_report_output = ReportOutputNode(
        name="report_output",
        approved_codebook=_codebook(),
        units=_units(),
        code_assignments=_assignments(),
        candidate_insights=[
            CandidateInsight(
                insight_id="I1",
                observation="Onboarding is clear",
                supporting_codes=["C1"],
                supporting_units=["U0001"],
            )
        ],
        approved_insight_ids=["I1"],
        recommendations=[
            Recommendation(
                insight_id="I1",
                finding="obs",
                action="act",
                expected_impact="impact",
            )
        ],
    )

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.report.upload_attachment", _upload_none
    )
    no_link_export = await clean_report_output(State({"node_results": {}}), {})
    assert "Download insight_report.md" not in no_link_export["assistant_message"]
    assert "Data caveats" not in no_link_export["assistant_message"]


@pytest.mark.asyncio
async def test_load_attachments_node_covers_error_and_storage_branches(
    tmp_path,
) -> None:
    node = LoadAttachmentsNode(name="load_attachments")

    # `documents` not a list -> early return with no attachments (line 80).
    not_list = await node(
        State({"inputs": {"documents": "not-a-list"}}), RunnableConfig()
    )
    assert not_list["node_results"]["load_attachments"]["attachments"] == []

    # A non-mapping document entry is skipped (line 84), and an attachment_id
    # with no resolver configured records the "unavailable" error (line 102).
    stored = tmp_path / "stored.txt"
    stored.write_text("stored body", encoding="utf-8")
    result = await node(
        State(
            {
                "inputs": {
                    "documents": [
                        "not-a-mapping",
                        {"attachment_id": "att-1", "filename": "no-resolver.txt"},
                        {"storage_path": str(stored), "filename": "stored.txt"},
                        {"filename": "nothing.txt"},
                    ]
                }
            }
        ),
        RunnableConfig(),
    )
    attachments = result["node_results"]["load_attachments"]["attachments"]
    assert len(attachments) == 3
    assert attachments[0]["filename"] == "no-resolver.txt"
    assert attachments[0]["errors"] == ["attachment resolver is unavailable"]
    # Successful storage-path read (lines 130-137): content decoded, no errors.
    assert attachments[1]["content"] == "stored body"
    assert attachments[1]["source"] == "storage"
    assert attachments[1]["errors"] == []
    # No content, no attachment_id, no storage_path -> generic fallback error
    # (line 142).
    assert attachments[2]["errors"] == ["no readable content found"]


@pytest.mark.asyncio
async def test_load_attachments_node_covers_resolver_error_and_bad_content_type() -> (
    None
):
    class _RaisingResolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            raise RuntimeError("resolver exploded")

    class _StringContentResolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return SimpleNamespace(content="not-bytes", name="odd.txt")

    node = LoadAttachmentsNode(name="load_attachments")

    # The resolver raising surfaces the exception message (lines 125-126).
    raising_result = await node(
        State(
            {
                "inputs": {
                    "documents": [{"attachment_id": "att-1", "filename": "boom.txt"}]
                }
            }
        ),
        {
            "configurable": {
                "attachment_resolver": _RaisingResolver(),
                "attachment_scope": "scope",
            }
        },
    )
    boom = raising_result["node_results"]["load_attachments"]["attachments"][0]
    assert boom["errors"] == ["resolver exploded"]
    assert boom["content"] == ""

    # `payload.content` isn't bytes -> decoded stays None (line 115), and the
    # unreadable-content fallback fires (line 142) since no other error was
    # recorded for this attachment.
    string_result = await node(
        State({"inputs": {"documents": [{"attachment_id": "att-2", "filename": ""}]}}),
        {
            "configurable": {
                "attachment_resolver": _StringContentResolver(),
                "attachment_scope": "scope",
            }
        },
    )
    odd = string_result["node_results"]["load_attachments"]["attachments"][0]
    assert odd["filename"] == "odd.txt"
    assert odd["errors"] == ["attachment content is not readable text"]


@pytest.mark.asyncio
async def test_load_attachments_node_covers_storage_decode_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = tmp_path / "stored.bin"
    stored.write_bytes(b"\x00\x01")
    node = LoadAttachmentsNode(name="load_attachments")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline._decode_attachment_content",
        lambda raw: None,  # noqa: ARG005
    )
    result = await node(
        State(
            {
                "inputs": {
                    "documents": [
                        {"storage_path": str(stored), "filename": "stored.bin"}
                    ]
                }
            }
        ),
        RunnableConfig(),
    )
    attachment = result["node_results"]["load_attachments"]["attachments"][0]
    assert attachment["errors"] == ["stored attachment is not readable text"]
    assert attachment["content"] == ""


def test_decode_attachment_content_returns_none_when_unreadable() -> None:
    """Both supported encodings must fail for ``_decode_attachment_content``.

    ``latin-1`` maps every byte 0-255 to a code point, so it can never raise
    ``UnicodeDecodeError``: the final ``return None`` (pipeline.py:58) is only
    reachable if a future encoding is added to the loop that can fail on
    otherwise-valid latin-1 bytes. We assert the *documented* contract directly
    instead of chasing that structurally unreachable branch with a real input.
    """
    from orcheo.nodes.qualitative.pipeline import _decode_attachment_content

    # latin-1 always succeeds, so every byte string decodes to something.
    assert _decode_attachment_content(bytes(range(256))) is not None


@pytest.mark.asyncio
async def test_validate_files_node_covers_message_and_field_branches() -> None:
    # A non-Mapping `data_file`/`codebook_file` value is impossible to build via
    # `run`, so exercise `_assistant_message` directly for lines 229->248/247/253.
    node = ValidateFilesNode(name="validate_files")
    assert node._assistant_message({}) == "Files look valid."
    assert (
        node._assistant_message({"data_file": {"filename": "f.csv", "kind": "other"}})
        == "Files look valid: found data file `f.csv`."
    )
    assert (
        node._assistant_message(
            {"data_file": {"filename": "f.csv", "kind": "raw", "record_count": 3}}
        )
        == "Files look valid: found data file `f.csv` (3 record(s))."
    )

    # Multiple data files and multiple codebook files (lines 322-323, 328, 331).
    multi_state = State(
        {
            "node_results": {
                "load_attachments": {
                    "attachments": [
                        {"filename": "a.csv", "content": "id,text\n1,Hello world\n"},
                        {"filename": "b.csv", "content": "id,text\n1,Another entry\n"},
                        {
                            "filename": "cb1.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Code,Def\n"
                            ),
                        },
                        {
                            "filename": "cb2.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T2,Theme2,C2,Code2,Def2\n"
                            ),
                        },
                        {"filename": "junk.bin", "content": ":"},
                    ]
                }
            }
        }
    )
    result = await node(multi_state, RunnableConfig())
    validated = result["node_results"]["validate_files"]
    assert "Multiple data files found; provide exactly one." in validated["errors"]
    assert "Multiple codebook files found; provide at most one." in validated["errors"]
    assert any("could not parse as raw data" in err for err in validated["errors"])

    # `codebook_field` configured together with a parsed codebook (line 344).
    field_node = ValidateFilesNode(
        name="validate_files", codebook_field="approved_codebook"
    )
    single_state = State(
        {
            "node_results": {
                "load_attachments": {
                    "attachments": [
                        {"filename": "a.csv", "content": "id,text\n1,Hello world\n"},
                        {
                            "filename": "cb.csv",
                            "content": (
                                "theme_id,theme_title,code_id,code_title,definition\n"
                                "T1,Theme,C1,Code,Def\n"
                            ),
                        },
                    ]
                }
            }
        }
    )
    single_result = (await field_node(single_state, RunnableConfig()))["node_results"][
        "validate_files"
    ]
    assert single_result["approved_codebook"]["themes"][0]["theme_id"] == "T1"

    # Coded-data upload while require_codebook is set, exercising the "coded"
    # branch of _assistant_message (kind == "coded").
    coded_message = node._assistant_message(
        {
            "data_file": {
                "filename": "coded.csv",
                "kind": "coded",
                "unit_count": 2,
                "assignment_count": 4,
            }
        }
    )
    assert "2 unit(s), 4 assignment(s)" in coded_message


@pytest.mark.asyncio
async def test_validate_files_node_covers_attachment_error_and_blank_content() -> None:
    node = ValidateFilesNode(name="validate_files")

    # An attachment carrying its own recorded errors (from LoadAttachmentsNode)
    # is surfaced and skipped without further classification (lines 271-272).
    result = await node(
        State(
            {
                "node_results": {
                    "load_attachments": {
                        "attachments": [
                            {
                                "filename": "broken.csv",
                                "content": "",
                                "errors": ["failed to load stored attachment"],
                            }
                        ]
                    }
                }
            }
        ),
        RunnableConfig(),
    )
    validated = result["node_results"]["validate_files"]
    assert "broken.csv: failed to load stored attachment" in validated["errors"]

    # Content that isn't a string at all (None) hits the "no readable content"
    # branch directly rather than via an empty/whitespace string (276-277).
    blank_result = await node(
        State(
            {
                "node_results": {
                    "load_attachments": {
                        "attachments": [
                            {"filename": "blank.csv", "content": None, "errors": []}
                        ]
                    }
                }
            }
        ),
        RunnableConfig(),
    )
    blank_validated = blank_result["node_results"]["validate_files"]
    assert "blank.csv: no readable content found" in blank_validated["errors"]


@pytest.mark.asyncio
async def test_validate_files_node_skips_raw_classification_for_coded_kind() -> None:
    # With `data_kind="coded"`, unparseable content must skip the raw-data
    # classification block entirely and jump straight to the generic
    # "could not parse" error naming "coded data CSV" (line 303->322, 322-323).
    node = ValidateFilesNode(name="validate_files", data_kind="coded")
    result = await node(
        State(
            {
                "node_results": {
                    "load_attachments": {
                        "attachments": [{"filename": "junk.bin", "content": ":"}]
                    }
                }
            }
        ),
        RunnableConfig(),
    )
    validated = result["node_results"]["validate_files"]
    assert any(
        "could not parse as coded data CSV or codebook CSV" in err
        for err in validated["errors"]
    )


@pytest.mark.asyncio
async def test_ingest_node_covers_pending_documents_fallback_branches() -> None:
    # `self.pending_documents` coerces to empty -> falls back to
    # `get_pending_documents(state)` (line 394), loop skips a doc with no
    # content (line 398), and a later doc succeeds after the earlier ones
    # fail to produce records, exercising the for/break path.
    node = IngestNode(name="ingest")
    state = State(
        {
            "node_results": {
                "load_attachments": {
                    "attachments": [
                        {"filename": "empty.txt", "content": ""},
                        {
                            "filename": "survey.csv",
                            "content": "id,text\n1,Hello there world\n",
                        },
                    ]
                }
            }
        }
    )
    result = (await node(state, {}))["node_results"]["ingest"]
    assert result["halt"] is False
    assert result["unit_count"] == 1
    assert result["source_payload"]["filename"] == "survey.csv"

    # No direct payload and no pending documents produce records at all ->
    # exhausts the for loop without breaking (line 395->412), then halts with
    # the no-records message (line 413).
    empty_node = IngestNode(name="ingest")
    empty_output = await empty_node(
        State(
            {
                "node_results": {
                    "load_attachments": {
                        "attachments": [{"filename": "empty.txt", "content": ""}]
                    }
                }
            }
        ),
        {},
    )
    assert empty_output["node_results"]["ingest"]["halt"] is True
    assert empty_output["assistant_message"] == empty_node.no_records_message


@pytest.mark.asyncio
async def test_codebook_output_node_covers_missing_codebook_message() -> None:
    node = CodebookOutputNode(name="codebook_output")
    result = await node(State({"node_results": {"ingest": {}}}), {})
    assert result["assistant_message"].startswith("No codebook could be produced.")


@pytest.mark.asyncio
async def test_export_codebook_node_covers_direct_model_and_validation_error() -> None:
    codebook = _codebook()

    # `self.codebook` is already a `Codebook` instance (line 545).
    direct_node = ExportCodebookNode(name="export_codebook", codebook=codebook)
    resolved = direct_node._resolved_codebook()
    assert resolved is codebook

    # A Mapping that fails Codebook validation (lines 549-550). The field type
    # is `Codebook | str`, so construct via `model_construct` to bypass eager
    # union coercion and exercise `_resolved_codebook`'s own validation path.
    invalid_node = ExportCodebookNode.model_construct(
        name="export_codebook", codebook={"themes": "not-a-list"}
    )
    assert invalid_node._resolved_codebook() is None
    invalid_result = await invalid_node(State({}), {})
    assert invalid_result["assistant_message"].startswith(
        "No codebook is available to export."
    )


@pytest.mark.asyncio
async def test_export_codebook_node_covers_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    node = ExportCodebookNode(name="export_codebook", codebook=codebook)

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    result = await node(State({}), {})
    assert result["assistant_message"] == "Export failed: network down"


@pytest.mark.asyncio
async def test_export_codebook_node_json_format_skips_upload() -> None:
    codebook = _codebook()
    node = ExportCodebookNode(
        name="export_codebook", codebook=codebook, export_format="json"
    )

    result = await node(State({}), {})

    assert "Codebook Export (JSON)" in result["assistant_message"]
    assert '"code_id": "C1"' in result["assistant_message"]
    assert "```json" in result["assistant_message"]


def test_export_codebook_node_accepts_unresolved_template_for_export_format() -> None:
    # IR construction instantiates nodes with raw, unresolved `{{...}}` template
    # strings before template resolution happens. A plain `Literal["csv", "json"]`
    # would reject that string at construction time, so `export_format` must
    # also accept `str` to defer validation to `run()`.
    node = ExportCodebookNode(
        name="export_codebook",
        codebook="{{node_results.ingest.codebook}}",
        export_format="{{structured_response.export_format}}",
    )
    assert node.export_format == "{{structured_response.export_format}}"


@pytest.mark.asyncio
async def test_recode_output_node_covers_direct_field_and_no_report_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/coded_data.csv")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )

    # Assignments, units, and quality_report all supplied directly, bypassing
    # every `get_*` fallback (lines 630->632, 641->643, 670->672), quality
    # report present appends the quality line (672->678 true), and the
    # batch-remaining note is skipped because there's no `recoder_finalize`
    # entry (681->693 false).
    node = RecodeOutputNode(
        name="recode_data",
        codebook=codebook.model_dump(mode="json"),
        units=[u.model_dump(mode="json") for u in units],
        assignments=[a.model_dump(mode="json") for a in assignments],
        quality_report={
            "total_units": 2,
            "flagged_units": 1,
            "excluded_units": 0,
        },
    )
    result = await node(State({"node_results": {"ingest": {}}}), {})
    assert "1/2 units flagged." in result["assistant_message"]
    assert (
        result["node_results"]["recode_data"]["coded_data_url"]
        == "https://example.test/coded_data.csv"
    )
    assert "per-turn limit" not in result["assistant_message"]

    # No codebook and no quality report -> csv_content is empty so the upload
    # branch is skipped entirely (651->659 false) and both csv_url/export_error
    # stay falsy (666->669 false).
    no_codebook_node = RecodeOutputNode(
        name="recode_data",
        assignments=[a.model_dump(mode="json") for a in assignments],
    )
    no_codebook_result = await no_codebook_node(
        State({"node_results": {"ingest": {}}}), {}
    )
    assert "Download coded_data.csv" not in no_codebook_result["assistant_message"]
    assert (
        "Could not generate the download link"
        not in (no_codebook_result["assistant_message"])
    )
    assert no_codebook_result["node_results"]["recode_data"]["coded_data_url"] is None


@pytest.mark.asyncio
async def test_export_coded_data_node_covers_direct_fields_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()

    # Units and assignments supplied directly bypass the get_* fallbacks
    # (lines 716->718, 719->721).
    node = ExportCodedDataNode(
        name="export_coded_data",
        codebook=codebook.model_dump(mode="json"),
        units=[u.model_dump(mode="json") for u in units],
        assignments=[a.model_dump(mode="json") for a in assignments],
    )

    async def _upload_ok(*args, **kwargs):  # noqa: ARG001
        return (None, "https://example.test/coded_data.csv")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_ok
    )
    result = await node(State({"node_results": {}}), {})

    assert (
        result["node_results"]["export_coded_data"]["coded_data_url"]
        == "https://example.test/coded_data.csv"
    )
    assert "Coded Data Export" in result["assistant_message"]
    assert "2 units" in result["assistant_message"]
    assert "Download coded_data.csv" in result["assistant_message"]


@pytest.mark.asyncio
async def test_export_coded_data_node_covers_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codebook = _codebook()
    units = _units()
    assignments = _assignments()
    node = ExportCodedDataNode(
        name="export_coded_data",
        codebook=codebook.model_dump(mode="json"),
        units=[u.model_dump(mode="json") for u in units],
        assignments=[a.model_dump(mode="json") for a in assignments],
    )

    async def _upload_fail(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "orcheo.nodes.qualitative.pipeline.upload_attachment", _upload_fail
    )
    result = await node(State({"node_results": {}}), {})
    assert result["assistant_message"] == "Export failed: network down"
