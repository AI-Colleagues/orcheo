"""Workflow tool progress update tests."""

from __future__ import annotations
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.ai.tools.context import (
    tool_execution_context,
    tool_progress_context,
)
from orcheo.nodes.ai import WorkflowTool, _create_workflow_tool_func


def _build_tool_graph() -> StateGraph:
    graph = StateGraph(State)

    def node_a(_state: State) -> dict[str, Any]:
        return {"results": {"node_a": {"ok": True}}}

    def node_b(_state: State) -> dict[str, Any]:
        return {"results": {"node_b": {"ok": True}}}

    graph.add_node("node_a", node_a)
    graph.add_node("node_b", node_b)
    graph.add_edge(START, "node_a")
    graph.add_edge("node_a", "node_b")
    graph.add_edge("node_b", END)
    return graph


@pytest.mark.asyncio
async def test_workflow_tool_emits_progress_updates() -> None:
    tool_graph = _build_tool_graph()
    tool_def = WorkflowTool(
        name="tool_graph",
        description="Test tool graph",
        graph=tool_graph,
    )
    compiled_graph = tool_def.get_compiled_graph()
    tool = _create_workflow_tool_func(
        compiled_graph=compiled_graph,
        name=tool_def.name,
        description=tool_def.description,
        args_schema=None,
    )

    updates: list[dict[str, Any]] = []

    async def progress_callback(step: dict[str, Any]) -> None:
        updates.append(step)

    config: RunnableConfig = {"configurable": {"thread_id": "tool-test"}}

    with tool_execution_context(config), tool_progress_context(progress_callback):
        result = await tool.coroutine()

    assert result
    assert updates
    assert any("node_a" in step for step in updates)
    assert any("node_b" in step for step in updates)


@pytest.mark.asyncio
async def test_workflow_tool_streaming_respects_graph_output_schema() -> None:
    """Streaming tool runs should still return the narrowed output schema."""

    class ToolOutput(TypedDict):
        answer: str

    graph = StateGraph(dict, output_schema=ToolOutput)

    def produce_answer(_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": "compact answer",
            "results": {"hidden_documents": [{"id": "1"}]},
        }

    graph.add_node("produce_answer", produce_answer)
    graph.add_edge(START, "produce_answer")
    graph.add_edge("produce_answer", END)

    tool = _create_workflow_tool_func(
        compiled_graph=graph.compile(),
        name="schema_tool",
        description="Schema-aware streaming tool",
        args_schema=None,
    )

    updates: list[dict[str, Any]] = []

    async def progress_callback(step: dict[str, Any]) -> None:
        updates.append(step)

    config: RunnableConfig = {"configurable": {"thread_id": "schema-tool"}}

    with tool_execution_context(config), tool_progress_context(progress_callback):
        result = await tool.ainvoke({"query": "hello"})

    assert result == {"answer": "compact answer"}
    assert updates == [{"produce_answer": {"answer": "compact answer"}}]


@pytest.mark.asyncio
async def test_run_tool_graph_without_progress_but_with_config() -> None:
    """Cover _run_tool_graph when progress_callback is None but config is set."""
    from orcheo.nodes.ai import _run_tool_graph

    tool_graph = _build_tool_graph()
    compiled = tool_graph.compile()

    config: RunnableConfig = {"configurable": {"thread_id": "cfg-only"}}

    with tool_execution_context(config):
        result = await _run_tool_graph(compiled, {})

    assert result is not None


@pytest.mark.asyncio
async def test_run_tool_graph_preserves_attachment_runtime_config() -> None:
    """Workflow-tool nodes should see attachment runtime objects in config."""
    from orcheo.nodes.ai import _run_tool_graph

    captured: dict[str, Any] = {}

    class CaptureNode(TaskNode):
        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            captured["configurable"] = dict((config or {}).get("configurable") or {})
            return {"ok": True}

    graph = StateGraph(State)
    graph.add_node("capture", CaptureNode(name="capture"))
    graph.add_edge(START, "capture")
    graph.add_edge("capture", END)
    compiled = graph.compile()

    class FakeResolver:
        pass

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "tool-test",
            "attachment_resolver": FakeResolver(),
            "attachment_scope": object(),
        }
    }

    with tool_execution_context(config):
        await _run_tool_graph(compiled, {"inputs": {}, "results": {}, "messages": []})

    assert "attachment_resolver" in captured["configurable"]
    assert "attachment_scope" in captured["configurable"]


@pytest.mark.asyncio
async def test_run_tool_graph_propagates_config_into_state() -> None:
    """Config from tool_execution_context should appear in sub-workflow state."""
    from orcheo.nodes.ai import _run_tool_graph

    captured_states: list[dict[str, Any]] = []

    graph = StateGraph(State)

    def capture_node(state: State) -> dict[str, Any]:
        captured_states.append(dict(state))
        return {}

    graph.add_node("capture", capture_node)
    graph.add_edge(START, "capture")
    graph.add_edge("capture", END)
    compiled = graph.compile()

    config: RunnableConfig = {
        "configurable": {
            "embed_model": "openai:text-embedding-3-small",
            "dimensions": 1536,
        }
    }

    with tool_execution_context(config):
        await _run_tool_graph(compiled, {"inputs": {}, "results": {}, "messages": []})

    assert captured_states, "Node was not executed"
    # Only the configurable portion is stored (full RunnableConfig is not
    # msgpack-serializable due to Runtime/callback objects in checkpoints).
    assert captured_states[0].get("config") == {"configurable": config["configurable"]}


@pytest.mark.asyncio
async def test_run_tool_graph_propagates_workspace_id_into_state() -> None:
    """Workspace id must be visible to nested workflow state helpers."""
    from orcheo.nodes.ai import _run_tool_graph

    captured_states: list[dict[str, Any]] = []

    graph = StateGraph(State)

    def capture_node(state: State) -> dict[str, Any]:
        captured_states.append(dict(state))
        return {}

    graph.add_node("capture", capture_node)
    graph.add_edge(START, "capture")
    graph.add_edge("capture", END)
    compiled = graph.compile()

    config: RunnableConfig = {
        "configurable": {
            "workspace_id": "workspace-123",
            "thread_id": "tool-test",
        }
    }

    with tool_execution_context(config):
        await _run_tool_graph(compiled, {"inputs": {}, "results": {}, "messages": []})

    assert captured_states, "Node was not executed"
    assert captured_states[0]["workspace_id"] == "workspace-123"


@pytest.mark.asyncio
async def test_run_tool_graph_strips_internal_configurable_keys() -> None:
    """Internal __pregel_* keys must be stripped from the state config."""
    from unittest.mock import AsyncMock
    from orcheo.nodes.ai.tools.context import tool_execution_context
    from orcheo.nodes.ai import _run_tool_graph

    config: RunnableConfig = {
        "configurable": {
            "user_key": "keep-me",
            "__pregel_runtime": "non-serializable-runtime",
            "__pregel_checkpointer": "non-serializable-checkpointer",
            "__pregel_store": "non-serializable-store",
        }
    }

    captured_payload: dict[str, Any] = {}
    fake_graph = AsyncMock()

    async def capture_ainvoke(payload: Any, **kwargs: Any) -> dict[str, Any]:
        captured_payload.update(payload)
        return {"inputs": {}, "results": {}, "messages": []}

    fake_graph.ainvoke = capture_ainvoke

    with tool_execution_context(config):
        await _run_tool_graph(fake_graph, {"inputs": {}, "results": {}, "messages": []})

    # Only user-facing configurable keys survive; __pregel_* are stripped.
    assert captured_payload["config"] == {"configurable": {"user_key": "keep-me"}}


@pytest.mark.asyncio
async def test_run_tool_graph_strips_attachment_runtime_keys() -> None:
    """Attachment runtime objects must not be persisted into graph state."""
    from unittest.mock import AsyncMock
    from orcheo.nodes.ai.tools.context import tool_execution_context
    from orcheo.nodes.ai import _run_tool_graph

    class FakeResolver:
        pass

    config: RunnableConfig = {
        "configurable": {
            "user_key": "keep-me",
            "attachment_resolver": FakeResolver(),
            "attachment_scope": object(),
        }
    }

    captured_payload: dict[str, Any] = {}
    fake_graph = AsyncMock()

    async def capture_ainvoke(payload: Any, **kwargs: Any) -> dict[str, Any]:
        captured_payload.update(payload)
        return {"inputs": {}, "results": {}, "messages": []}

    fake_graph.ainvoke = capture_ainvoke

    with tool_execution_context(config):
        await _run_tool_graph(fake_graph, {"inputs": {}, "results": {}, "messages": []})

    assert captured_payload["config"] == {"configurable": {"user_key": "keep-me"}}


@pytest.mark.asyncio
async def test_run_tool_graph_streaming_no_values_raises() -> None:
    """Cover _run_tool_graph RuntimeError when streaming yields no values."""
    from unittest.mock import AsyncMock
    from orcheo.nodes.ai import _run_tool_graph

    # Create a fake compiled graph whose astream yields updates but no values
    fake_graph = AsyncMock()

    async def fake_astream(*_args, **_kwargs):
        yield ("updates", {"node_a": {"ok": True}})

    fake_graph.astream = fake_astream

    config: RunnableConfig = {"configurable": {"thread_id": "no-values"}}

    async def noop_progress(step: dict[str, Any]) -> None:
        pass

    with tool_execution_context(config), tool_progress_context(noop_progress):
        with pytest.raises(RuntimeError, match="did not produce final values"):
            await _run_tool_graph(fake_graph, {})
