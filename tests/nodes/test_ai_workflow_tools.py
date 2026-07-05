"""Workflow tool behavior tests for AI nodes."""

from __future__ import annotations
from unittest.mock import patch
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from typing_extensions import TypedDict
from orcheo.graph.state import State
from orcheo.nodes.ai.workflow_tools import (
    WorkflowTool,
    _create_workflow_tool_func,
)


@pytest.mark.asyncio
@patch("orcheo.nodes.ai.create_agent")
@patch("orcheo.nodes.ai.MultiServerMCPClient")
async def test_prepare_tools_with_workflow_tools(
    mock_mcp_client_class, mock_create_agent, agent, mock_agent, mock_mcp_client
):
    """Workflow tools should be included when provided."""

    from langgraph.graph import StateGraph

    mock_mcp_client_class.return_value = mock_mcp_client
    mock_mcp_client.get_tools.return_value = []
    mock_create_agent.return_value = mock_agent

    workflow_graph = StateGraph(dict)

    def simple_node(state):
        return {"result": "workflow result"}

    workflow_graph.add_node("start", simple_node)
    workflow_graph.set_entry_point("start")
    workflow_graph.set_finish_point("start")

    class WorkflowArgs(BaseModel):
        input_value: str

    workflow_tool = WorkflowTool(
        name="test_workflow",
        description="A test workflow tool",
        graph=workflow_graph,
        args_schema=WorkflowArgs,
    )

    agent.predefined_tools = []
    agent.workflow_tools = [workflow_tool]
    state: State = {"messages": [{"role": "user", "content": "test"}]}
    config = RunnableConfig()

    await agent.run(state, config)

    mock_create_agent.assert_called_once()
    call_kwargs = mock_create_agent.call_args[1]
    assert len(call_kwargs["tools"]) == 1
    assert call_kwargs["tools"][0].name == "test_workflow"


@pytest.mark.asyncio
@patch("orcheo.nodes.ai.create_agent")
@patch("orcheo.nodes.ai.MultiServerMCPClient")
async def test_prepare_tools_with_workflow_tools_no_args_schema(
    mock_mcp_client_class, mock_create_agent, agent, mock_agent, mock_mcp_client
):
    """Workflow tools without schemas should still be wired up."""

    from langgraph.graph import StateGraph

    mock_mcp_client_class.return_value = mock_mcp_client
    mock_mcp_client.get_tools.return_value = []
    mock_create_agent.return_value = mock_agent

    workflow_graph = StateGraph(dict)

    def simple_node(state):
        return {"result": "workflow result"}

    workflow_graph.add_node("start", simple_node)
    workflow_graph.set_entry_point("start")
    workflow_graph.set_finish_point("start")

    workflow_tool = WorkflowTool(
        name="test_workflow_no_schema",
        description="A test workflow tool without schema",
        graph=workflow_graph,
        args_schema=None,
    )

    agent.predefined_tools = []
    agent.workflow_tools = [workflow_tool]
    state: State = {"messages": [{"role": "user", "content": "test"}]}
    config = RunnableConfig()

    await agent.run(state, config)

    mock_create_agent.assert_called_once()
    call_kwargs = mock_create_agent.call_args[1]
    assert len(call_kwargs["tools"]) == 1
    assert call_kwargs["tools"][0].name == "test_workflow_no_schema"


@pytest.mark.asyncio
@patch("orcheo.nodes.ai.create_agent")
@patch("orcheo.nodes.ai.MultiServerMCPClient")
async def test_workflow_tool_compilation_caching(
    mock_mcp_client_class, mock_create_agent, agent, mock_agent, mock_mcp_client
):
    """WorkflowTool should compile graphs once and reuse them."""

    from langgraph.graph import StateGraph

    mock_mcp_client_class.return_value = mock_mcp_client
    mock_mcp_client.get_tools.return_value = []
    mock_create_agent.return_value = mock_agent

    workflow_graph = StateGraph(dict)

    def simple_node(state):
        return {"result": "workflow result"}

    workflow_graph.add_node("start", simple_node)
    workflow_graph.set_entry_point("start")
    workflow_graph.set_finish_point("start")

    workflow_tool = WorkflowTool(
        name="cached_workflow",
        description="A workflow with cached compilation",
        graph=workflow_graph,
    )

    compiled_1 = workflow_tool.get_compiled_graph()
    compiled_2 = workflow_tool.get_compiled_graph()
    assert compiled_1 is compiled_2

    agent.predefined_tools = []
    agent.workflow_tools = [workflow_tool]
    state: State = {"messages": [{"role": "user", "content": "test"}]}
    config = RunnableConfig()

    await agent.run(state, config)

    mock_create_agent.assert_called_once()
    call_kwargs = mock_create_agent.call_args[1]
    assert len(call_kwargs["tools"]) == 1


@pytest.mark.asyncio
async def test_workflow_tool_async_execution():
    """Async execution path should invoke compiled graph via ainvoke."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    execution_tracker = {"executed": False}

    def test_node(state):
        execution_tracker["executed"] = True
        return {"result": "async_executed", "input_data": state}

    workflow_graph.add_node("process", test_node)
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    compiled_graph = workflow_graph.compile()

    tool = _create_workflow_tool_func(
        compiled_graph=compiled_graph,
        name="test_async_tool",
        description="Test async execution",
        args_schema=None,
    )

    result = await tool.ainvoke({"test_input": "test_value"})

    assert execution_tracker["executed"]
    assert result["result"] == "async_executed"


def test_workflow_tool_sync_execution():
    """Sync execution path should funnel through asyncio.run."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    execution_tracker = {"executed": False}

    def test_node(state):
        execution_tracker["executed"] = True
        return {"result": "sync_executed", "input_data": state}

    workflow_graph.add_node("process", test_node)
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    compiled_graph = workflow_graph.compile()

    tool = _create_workflow_tool_func(
        compiled_graph=compiled_graph,
        name="test_sync_tool",
        description="Test sync execution",
        args_schema=None,
    )

    result = tool.invoke({"test_input": "sync_test"})

    assert execution_tracker["executed"]
    assert result["result"] == "sync_executed"


@pytest.mark.asyncio
async def test_workflow_tool_respects_graph_output_schema() -> None:
    """Graph output_schema should narrow the tool payload automatically."""

    from langgraph.graph import StateGraph

    class ToolOutput(TypedDict):
        answer: str

    workflow_graph = StateGraph(dict, output_schema=ToolOutput)

    def test_node(_state):
        return {
            "answer": "compact answer",
            "node_results": {"hidden_documents": [{"id": "1"}]},
        }

    workflow_graph.add_node("process", test_node)
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="schema_tool",
        description="Test output schema narrowing",
        args_schema=None,
    )

    result = await tool.ainvoke({"query": "hello"})

    assert result == {"answer": "compact answer"}


@pytest.mark.asyncio
async def test_workflow_tool_output_path_selects_nested_value() -> None:
    """WorkflowTool output_path should return a selected nested result only."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)

    def test_node(_state):
        return {
            "node_results": {
                "format_results": {
                    "markdown": "formatted answer",
                    "documents": [{"id": "1"}],
                }
            }
        }

    workflow_graph.add_node("process", test_node)
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="Test selector output",
        args_schema=None,
        output_path="node_results.format_results.markdown",
    )

    result = await tool.ainvoke({"query": "hello"})

    assert result == "formatted answer"


@pytest.mark.asyncio
async def test_workflow_tool_output_path_errors_when_missing() -> None:
    """Missing output_path segments should raise a clear error."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    workflow_graph.add_node("process", lambda _state: {"node_results": {}})
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="Test selector output",
        args_schema=None,
        output_path="node_results.format_results.markdown",
    )

    with pytest.raises(ValueError, match="output_path"):
        await tool.ainvoke({"query": "hello"})


@pytest.mark.asyncio
async def test_workflow_tool_output_path_handles_list_segments() -> None:
    """List-based output_path segments should resolve numeric indexes."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)

    def test_node(_state):
        return {
            "items": [
                {"value": "first"},
                {"value": "second"},
            ]
        }

    workflow_graph.add_node("process", test_node)
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="List segment selector",
        args_schema=None,
        output_path="items.1.value",
    )

    result = await tool.ainvoke({"query": "hello"})
    assert result == "second"


@pytest.mark.asyncio
async def test_workflow_tool_output_path_requires_integer_list_segments() -> None:
    """List traversal requires integer segments with clear messaging."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    workflow_graph.add_node("process", lambda _state: {"items": [{"value": "ok"}]})
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="List segment selector",
        args_schema=None,
        output_path="items.one.value",
    )

    with pytest.raises(ValueError, match="requires an integer segment"):
        await tool.ainvoke({"query": "hello"})


@pytest.mark.asyncio
async def test_workflow_tool_output_path_list_index_out_of_range() -> None:
    """Out-of-range list indexes should raise an informative error."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    workflow_graph.add_node(
        "process",
        lambda _state: {"items": [{"value": "first"}]},
    )
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="List segment selector",
        args_schema=None,
        output_path="items.2.value",
    )

    with pytest.raises(ValueError, match="index 2 is out of range"):
        await tool.ainvoke({"query": "hello"})


@pytest.mark.asyncio
async def test_workflow_tool_output_path_cannot_descend_into_scalar() -> None:
    """Non-iterable payload segments should raise a clear error."""

    from langgraph.graph import StateGraph

    workflow_graph = StateGraph(dict)
    workflow_graph.add_node("process", lambda _state: {"value": "scalar"})
    workflow_graph.set_entry_point("process")
    workflow_graph.set_finish_point("process")

    tool = _create_workflow_tool_func(
        compiled_graph=workflow_graph.compile(),
        name="selector_tool",
        description="List segment selector",
        args_schema=None,
        output_path="value.key",
    )

    with pytest.raises(ValueError, match="cannot descend into str"):
        await tool.ainvoke({"query": "hello"})


def test_workflow_tool_output_path_validator_trims_whitespace() -> None:
    """Whitespace-only output paths should be rejected after trimming."""

    normalized = WorkflowTool._validate_output_path("  path ")
    assert normalized == "path"


def test_workflow_tool_output_path_validator_rejects_empty_string() -> None:
    """Empty output paths must raise a validation error."""

    with pytest.raises(ValueError, match="output_path must not be empty"):
        WorkflowTool._validate_output_path("   ")


def test_workflow_tool_return_direct_defaults_false() -> None:
    """return_direct should default to False to preserve existing behaviour."""

    from langgraph.graph import StateGraph

    graph = StateGraph(dict)
    graph.add_node("noop", lambda state: {"ok": True})
    graph.set_entry_point("noop")
    graph.set_finish_point("noop")

    tool = WorkflowTool(name="t", description="d", graph=graph)
    assert tool.return_direct is False


def test_create_workflow_tool_func_propagates_return_direct() -> None:
    """return_direct must be forwarded onto the underlying StructuredTool."""

    from langgraph.graph import StateGraph

    graph = StateGraph(dict)
    graph.add_node("noop", lambda state: {"ok": True})
    graph.set_entry_point("noop")
    graph.set_finish_point("noop")
    compiled = graph.compile()

    direct = _create_workflow_tool_func(
        compiled_graph=compiled,
        name="direct_tool",
        description="d",
        args_schema=None,
        return_direct=True,
    )
    indirect = _create_workflow_tool_func(
        compiled_graph=compiled,
        name="indirect_tool",
        description="d",
        args_schema=None,
    )
    assert direct.return_direct is True
    assert indirect.return_direct is False
