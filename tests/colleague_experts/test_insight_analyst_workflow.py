"""Regression tests for the Insight Analyst workflow helpers."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from orcheo.graph.state import State
from orcheo.nodes.qualitative.codebook import recover_exportable_codebook
from orcheo.nodes.qualitative.pipeline import ContextPreNode, SetupNode

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / "colleague-experts"
    / "colleagues"
    / "insight_analyst"
    / "workflow.py"
)

if not _WORKFLOW_PATH.exists():
    pytest.skip(
        "colleague-experts repo not checked out alongside orcheo",
        allow_module_level=True,
    )


def _load_workflow_module():
    module_name = "insight_analyst_workflow"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = spec_from_file_location(module_name, _WORKFLOW_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load workflow module from {_WORKFLOW_PATH}"
        raise RuntimeError(msg)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[module_name]
        raise
    return module


@pytest.mark.asyncio
async def test_insight_analyst_workflow_builds_and_compiles() -> None:
    workflow = _load_workflow_module()

    graph = await workflow.orcheo_workflow()
    compiled = graph.compile()

    assert compiled is not None


@pytest.mark.asyncio
async def test_context_pre_loads_attachment_content_from_resolver() -> None:
    class _Attachment:
        def __init__(self, content: bytes, name: str) -> None:
            self.content = content
            self.name = name

    class _Resolver:
        async def load_attachment_bytes(self, attachment_id, attachment_scope):  # noqa: ARG002
            return _Attachment(b"respondent_id,text\nR1,hello\n", "survey.csv")

    context_pre = ContextPreNode(name="context_pre")
    result = await context_pre(
        State(
            {
                "inputs": {
                    "documents": [
                        {
                            "attachment_id": "att-1",
                            "filename": "",
                        }
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

    context_result = result["results"]["context_pre"]
    assert context_result["source_hint"] == "1 file(s) loaded: survey.csv"
    assert context_result["pending_documents"][0]["filename"] == "survey.csv"
    assert context_result["pending_documents"][0]["content"] == (
        "respondent_id,text\nR1,hello\n"
    )


@pytest.mark.asyncio
async def test_setup_node_resolves_source_and_codebook_from_pending_documents() -> None:
    setup = SetupNode(
        name="setup",
        resolve_codebook=True,
        resolve_seed_codebook=True,
        exclude_codebook_docs=True,
    )
    state = State(
        {
            "inputs": {"research_objective": "Understand onboarding pain points"},
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
                            "content": "respondent_id,text\nR1,Hello\n",
                        },
                    ]
                }
            },
        }
    )

    result = (await setup(state, {}))["results"]["setup"]

    assert result["objective"] == "Understand onboarding pain points"
    assert result["source_payload"]["filename"] == "survey.csv"
    assert result["approved_codebook"]["themes"][0]["theme_id"] == "T1"
    assert result["seed_codebook_from_file"]["themes"][0]["theme_id"] == "T1"


def test_recover_exportable_codebook_uses_assistant_message_history() -> None:
    state = State(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "# Insight Analyst - Draft Codebook\n\n"
                        "| Theme ID | Theme Title | Code ID | Code Title | Definition | "
                        "Include | Exclude |\n"
                        "| --- | --- | --- | --- | --- | --- | --- |\n"
                        "| T1 | Navigation and instruction clarity | T1.C1 | "
                        "Confusing setup | The onboarding/setup process is hard to "
                        "understand. | Statements that the setup is confusing. | "
                        "Problems caused primarily by technical failures. |\n"
                    ),
                }
            ]
        }
    )

    codebook = recover_exportable_codebook(state)

    assert codebook is not None
    assert codebook.themes[0].theme_id == "T1"
