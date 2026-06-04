"""Focused tests for AgentNode runtime helper branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orcheo.nodes.ai import AgentNode
from orcheo.nodes.ai import agent as agent_module


def test_tool_graph_payload_with_runtime_config_strips_private_keys() -> None:
    payload = {"messages": ["hello"]}
    config = {
        "configurable": {
            "workspace_id": " workspace-1 ",
            "attachment_resolver": "resolver",
            "attachment_scope": "scope",
            "attachment_uploader": "uploader",
            "__private": "hidden",
            "custom": "value",
        }
    }

    result = agent_module._tool_graph_payload_with_runtime_config(payload, config)

    assert result["workspace_id"] == "workspace-1"
    assert result["config"]["configurable"] == {
        "workspace_id": " workspace-1 ",
        "custom": "value",
    }


def test_tool_graph_payload_with_runtime_config_carries_thread_state() -> None:
    payload = {"messages": ["hello"]}
    config = {
        "configurable": {
            "workspace_id": "workspace-1",
            "thread_state": {
                "pending_documents": [{"filename": "survey.csv"}],
            },
        }
    }

    result = agent_module._tool_graph_payload_with_runtime_config(payload, config)

    assert result["thread_state"] == {
        "pending_documents": [{"filename": "survey.csv"}],
    }


def test_tool_graph_payload_with_runtime_config_ignores_blank_workspace_id() -> None:
    payload = {"messages": ["hello"]}
    config = {"configurable": {"workspace_id": "   ", "custom": "value"}}

    result = agent_module._tool_graph_payload_with_runtime_config(payload, config)

    assert "workspace_id" not in result
    assert result["config"]["configurable"]["workspace_id"] == "   "


@pytest.mark.asyncio
async def test_stream_tool_graph_updates_uses_tuple_events() -> None:
    class DummyGraph:
        output_channels = ["messages"]

        async def astream(self, payload: dict[str, object], **kwargs: object):
            assert payload == {"input": "value"}
            assert kwargs["stream_mode"] == ["updates", "values"]
            assert kwargs["output_keys"] == ["messages"]
            yield ("updates", {"step": 1})
            yield ("values", {"final": True})

    progress_callback = AsyncMock()

    result = await agent_module._stream_tool_graph_updates(
        DummyGraph(),
        {"input": "value"},
        {"configurable": {}},
        progress_callback,
    )

    progress_callback.assert_awaited_once_with({"step": 1})
    assert result == {"final": True}


@pytest.mark.asyncio
async def test_stream_tool_graph_updates_uses_plain_update_events() -> None:
    class DummyGraph:
        async def astream(self, payload: dict[str, object], **kwargs: object):
            assert payload == {"input": "value"}
            assert kwargs["stream_mode"] == ["updates", "values"]
            yield {"step": 1}
            yield ("values", {"final": True})

    progress_callback = AsyncMock()

    result = await agent_module._stream_tool_graph_updates(
        DummyGraph(),
        {"input": "value"},
        {"configurable": {}},
        progress_callback,
    )

    progress_callback.assert_awaited_once_with({"step": 1})
    assert result == {"final": True}


def test_build_runtime_config_merges_mapping_and_strips_workspace_id() -> None:
    node = AgentNode(name="agent", ai_model="openai:gpt-4o")
    state = {
        "workspace_id": " workspace-1 ",
        "inputs": {
            "documents": [{"filename": "survey.csv"}],
        },
        "results": {
            "_thread_state": {
                "pending_documents": [{"filename": "survey.csv"}],
            }
        },
    }
    config = {"configurable": {"custom": "value"}}

    result = node._build_runtime_config(state, config)

    assert result["configurable"]["workspace_id"] == "workspace-1"
    assert result["configurable"]["custom"] == "value"
    assert result["configurable"]["inputs"] == {
        "documents": [{"filename": "survey.csv"}],
    }
    assert result["configurable"]["thread_state"] == {
        "pending_documents": [{"filename": "survey.csv"}],
    }


def test_build_runtime_config_handles_non_mapping_config() -> None:
    node = AgentNode(name="agent", ai_model="openai:gpt-4o")
    state = {"workspace_id": "   "}

    result = node._build_runtime_config(state, object())

    assert result["configurable"] == {}


def test_set_run_trace_metadata_ignores_non_mapping_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = AgentNode(name="agent", ai_model="openai:gpt-4o")
    recorder = MagicMock()
    monkeypatch.setattr(
        AgentNode, "_set_trace_metadata_for_run", recorder, raising=False
    )

    node._set_run_trace_metadata("model", "not-a-mapping")

    recorder.assert_not_called()


@pytest.mark.asyncio
async def test_persist_graph_history_for_run_ignores_non_mapping_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = AgentNode(
        name="agent", ai_model="openai:gpt-4o", use_graph_chat_history=True
    )
    recorder = AsyncMock()
    monkeypatch.setattr(AgentNode, "_persist_graph_history", recorder, raising=False)

    await node._persist_graph_history_for_run(
        current_messages=[],
        messages=[],
        result="not-a-mapping",
        history_store=object(),
        history_namespace=("ns",),
        history_key="key",
    )

    recorder.assert_not_awaited()
