"""Tests for AI node helpers and utilities."""

from __future__ import annotations
import contextlib
import sys
from types import SimpleNamespace
import pytest
from orcheo.nodes.ai import (
    AgentNode,
    LLMNode,
    _llm_trace_metadata,
    _select_workflow_tool_output,
)


def test_llm_trace_metadata_prefers_result_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_chat_result_model_name", lambda payload: "result-model"
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_model_name_from_instance", lambda model: "instance-model"
    )

    metadata = _llm_trace_metadata(
        "provider:model", model=SimpleNamespace(model_name="ignored"), result={}
    )

    assert metadata["ai"]["actual_model"] == "result-model"


def test_llm_trace_metadata_falls_back_to_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_chat_result_model_name", lambda payload: None
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_model_name_from_instance", lambda model: "instance-model"
    )

    metadata = _llm_trace_metadata(
        "provider:model",
        model=SimpleNamespace(model_name="instance-model"),
        result=None,
    )

    assert metadata["ai"]["actual_model"] == "instance-model"


def test_select_workflow_tool_output_handles_nested_paths() -> None:
    payload = {"a": {"b": [0, {"c": "value"}]}}

    assert _select_workflow_tool_output(payload, "a.b.1.c", "tool") == "value"

    with pytest.raises(ValueError, match="could not resolve segment"):
        _select_workflow_tool_output(payload, "a.x", "tool")
    with pytest.raises(ValueError, match="requires an integer segment"):
        _select_workflow_tool_output(payload, "a.b.x", "tool")
    with pytest.raises(ValueError, match="index 5 is out of range"):
        _select_workflow_tool_output(payload, "a.b.5", "tool")


def test_ai_package_falls_back_to_impl_module() -> None:
    from orcheo.nodes.ai.agent import _ai_package

    package = sys.modules.pop("orcheo.nodes.ai", None)
    try:
        module = _ai_package()
        assert module.__name__ == "orcheo.nodes.ai.agent"
    finally:
        if package is not None:
            sys.modules["orcheo.nodes.ai"] = package
    with pytest.raises(ValueError, match="cannot descend"):
        _select_workflow_tool_output(
            {"a": {"b": [0, {"c": "value"}]}},
            "a.b.0.x",
            "tool",
        )


@pytest.mark.asyncio
async def test_agent_node_run_sets_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"messages": [], "response_metadata": {"model": "result-model"}}

    class FakeAgent:
        async def ainvoke(self, payload, config):
            return response

    fake_agent = FakeAgent()
    fake_model = SimpleNamespace(model_name="provider:model")

    monkeypatch.setattr(
        "orcheo.nodes.ai.init_chat_model", lambda ai_model, **kwargs: fake_model
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.tool_execution_context", contextlib.nullcontext
    )
    monkeypatch.setattr("orcheo.nodes.ai._get_graph_store_fn", lambda config: None)
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_chat_result_model_name", lambda payload: "result-model"
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_model_name_from_instance", lambda model: "instance-model"
    )

    node = AgentNode(name="agent", ai_model="provider:model")

    result = await node.run(state={"messages": []}, config={})

    assert result == response


@pytest.mark.asyncio
async def test_llm_node_run_attaches_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"messages": []}

    class FakeAgent:
        async def ainvoke(self, payload, config):
            return response

    fake_agent = FakeAgent()
    fake_model = SimpleNamespace(model_name="provider:model")

    monkeypatch.setattr(
        "orcheo.nodes.ai.init_chat_model", lambda ai_model, **kwargs: fake_model
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.create_agent", lambda *args, **kwargs: fake_agent
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.tool_execution_context", contextlib.nullcontext
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_chat_result_model_name", lambda payload: "result-model"
    )
    monkeypatch.setattr(
        "orcheo.nodes.ai.infer_model_name_from_instance", lambda model: "instance-model"
    )

    node = LLMNode(name="llm", ai_model="provider:model", input_text="hello")

    result = await node.run(state={}, config=None)

    assert result == response
