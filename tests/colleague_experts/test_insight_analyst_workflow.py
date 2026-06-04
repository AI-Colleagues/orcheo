"""Regression tests for the Insight Analyst workflow helpers."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_workflow_module():
    module_name = "insight_analyst_workflow"
    if module_name in sys.modules:
        return sys.modules[module_name]

    workflow_path = (
        Path(__file__).resolve().parents[2]
        / "colleague-experts"
        / "colleagues"
        / "insight_analyst"
        / "workflow.py"
    )
    spec = spec_from_file_location(module_name, workflow_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load workflow module from {workflow_path}"
        raise RuntimeError(msg)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_thread_id_uses_attachment_scope_thread_id() -> None:
    workflow = _load_workflow_module()
    config = {
        "configurable": {
            "thread_id": "exec-123",
            "attachment_scope": {
                "workspace_id": "ws-1",
                "thread_id": "chat-456",
                "workflow_id": "wf-789",
            },
        }
    }
    state = {
        "workspace_id": "ws-1",
        "config": config,
        "inputs": {},
    }

    assert workflow.extract_thread_id(state) == "chat-456"
    assert workflow.extract_thread_id_from_config(config) == "chat-456"
    assert workflow.extract_workspace_id(state) == "ws-1"
    assert workflow.extract_workspace_id_from_config(config) == "ws-1"
    assert workflow.resolve_thread_namespace(state, config) == (
        "ws-1",
        workflow.THREAD_NAMESPACE_TAIL,
        "chat-456",
    )


@pytest.mark.asyncio
async def test_load_pending_documents_uses_configurable_inputs() -> None:
    workflow = _load_workflow_module()
    state = {
        "config": {
            "configurable": {
                "inputs": {
                    "documents": [
                        {
                            "filename": "survey.csv",
                            "content": "respondent_id,text\nR1,hello\n",
                        }
                    ]
                }
            }
        }
    }

    pending = await workflow.load_pending_documents_from_state(state, None)

    assert len(pending) == 1
    assert pending[0]["filename"] == "survey.csv"
    assert pending[0]["content"] == "respondent_id,text\nR1,hello\n"


@pytest.mark.asyncio
async def test_load_pending_documents_deduplicates_by_attachment_id() -> None:
    """Same attachment_id appearing in both state.inputs and state.config.configurable.inputs
    should produce exactly one pending document, not two."""
    workflow = _load_workflow_module()
    doc = {
        "attachment_id": "atc_abc123",
        "source": "survey.csv",
        "content": "r,t\n1,hello\n",
    }
    state = {
        "inputs": {"documents": [doc]},
        "config": {
            "configurable": {
                "inputs": {"documents": [doc]},
            }
        },
    }

    pending = await workflow.load_pending_documents_from_state(state, None)

    assert len(pending) == 1
    assert pending[0]["attachment_id"] == "atc_abc123"


@pytest.mark.asyncio
async def test_codebook_setup_loads_raw_source_from_pending_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _load_workflow_module()
    saved_thread_state = {}

    async def fake_save_thread_state(state, config, thread_state) -> None:  # noqa: ANN001
        saved_thread_state["value"] = thread_state

    async def fake_load_thread_state(state, config):  # noqa: ANN001
        return workflow.ThreadState(
            pending_documents=[
                {
                    "filename": "survey.csv",
                    "content": (
                        "respondent_id,segment,role,text\n"
                        'R1,new,admin,"The setup checklist was clear."\n'
                    ),
                }
            ]
        )

    async def fake_load_pending_documents_from_state(state, config):  # noqa: ANN001
        return [
            {
                "filename": "survey.csv",
                "content": (
                    "respondent_id,segment,role,text\n"
                    'R1,new,admin,"The setup checklist was clear."\n'
                ),
            }
        ]

    monkeypatch.setattr(workflow, "save_thread_state", fake_save_thread_state)
    monkeypatch.setattr(workflow, "load_thread_state", fake_load_thread_state)
    monkeypatch.setattr(
        workflow,
        "load_pending_documents_from_state",
        fake_load_pending_documents_from_state,
    )

    node = workflow.CodebookSetupNode(name="codebook_setup")
    result = await node.run({"inputs": {}}, None)

    assert result == {"objective": "(not provided)"}
    assert "value" in saved_thread_state
    assert saved_thread_state["value"].source_payload == {
        "content": (
            "respondent_id,segment,role,text\n"
            'R1,new,admin,"The setup checklist was clear."\n'
        ),
        "filename": "survey.csv",
        "source_type": "survey_csv",
        "storage_path": None,
    }


@pytest.mark.asyncio
async def test_recode_setup_recovers_codebook_from_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recode setup falls back to the prior assistant codebook output."""
    workflow = _load_workflow_module()
    saved_thread_state = {}

    async def fake_save_thread_state(state, config, thread_state) -> None:  # noqa: ANN001
        saved_thread_state["value"] = thread_state

    async def fake_load_thread_state(state, config):  # noqa: ANN001
        return workflow.ThreadState()

    async def fake_load_pending_documents_from_state(state, config):  # noqa: ANN001
        return []

    monkeypatch.setattr(workflow, "save_thread_state", fake_save_thread_state)
    monkeypatch.setattr(workflow, "load_thread_state", fake_load_thread_state)
    monkeypatch.setattr(
        workflow,
        "load_pending_documents_from_state",
        fake_load_pending_documents_from_state,
    )

    codebook_markdown = (
        "# Insight Analyst — Draft Codebook\n\n"
        "| Theme ID | Theme Title | Code ID | Code Title | Definition | Include | Exclude |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| T1 | Navigation and instruction clarity | T1.C1 | Confusing setup | "
        "The onboarding/setup process is hard to understand. | Statements that "
        "the setup is confusing. | Problems caused primarily by technical "
        "failures. |\n"
    )
    state = {
        "messages": [
            {"role": "assistant", "content": codebook_markdown},
        ]
    }

    node = workflow.RecodeSetupNode(name="recode_setup")
    result = await node.run(state, None)

    assert result == {"has_source": False, "has_codebook": True}
    assert "value" in saved_thread_state
    assert saved_thread_state["value"].approved_codebook is not None
    assert saved_thread_state["value"].approved_codebook.themes[0].theme_id == "T1"
