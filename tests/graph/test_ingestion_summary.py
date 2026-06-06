"""Tests for LangGraph summary serialization."""

from __future__ import annotations
import json
import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from orcheo.graph.ingestion import summary as summary_module
from orcheo.graph.ingestion.summary import summarise_graph_index, summarise_state_graph
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode, WorkflowTool
from orcheo.nodes.connectors.qq import QQBotListenerNode
from orcheo.nodes.connectors.telegram import TelegramBotListenerNode
from orcheo.nodes.triggers import CronTriggerNode


class ToolInput(BaseModel):
    """Input model for the workflow tool."""

    query: str


def _build_tool_graph() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    return graph


def test_summarise_state_graph_handles_workflow_tools_graph() -> None:
    graph = StateGraph(State)
    tool_graph = _build_tool_graph()
    agent = AgentNode(
        name="agent",
        ai_model="gpt-4o-mini",
        workflow_tools=[
            WorkflowTool(
                name="tool",
                description="tool desc",
                graph=tool_graph,
                args_schema=ToolInput,
            )
        ],
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)

    summary = summarise_state_graph(graph)
    json.dumps(summary)

    agent_node = next(node for node in summary["nodes"] if node["name"] == "agent")
    workflow_tools = agent_node["workflow_tools"]
    assert workflow_tools[0]["graph"]["type"] == "StateGraph"
    assert workflow_tools[0]["graph"]["summary"]["nodes"] == [
        {"name": "noop", "type": "RunnableCallable"},
    ]
    assert workflow_tools[0]["graph"]["summary"]["edges"] == [
        ["START", "noop"],
        ["noop", "END"],
    ]


def test_serialise_fallback_nested_basemodel() -> None:
    from orcheo.graph.ingestion.summary import _serialise_fallback

    class Inner(BaseModel):
        value: int = 1

    result = _serialise_fallback(Inner())
    assert result == {"value": 1}


def test_serialise_fallback_compiled_state_graph() -> None:
    from orcheo.graph.ingestion.summary import _serialise_fallback

    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    compiled = graph.compile()
    result = _serialise_fallback(compiled)
    assert result == {"type": "CompiledStateGraph"}


def test_serialise_fallback_set() -> None:
    from orcheo.graph.ingestion.summary import _serialise_fallback

    result = _serialise_fallback({1, 2})
    assert isinstance(result, list)
    assert sorted(result) == ["1", "2"]


def test_serialise_fallback_unknown_type() -> None:
    from orcheo.graph.ingestion.summary import _serialise_fallback

    class Custom:
        pass

    obj = Custom()
    result = _serialise_fallback(obj)
    assert "Custom" in result


def test_summarise_graph_index_contains_mermaid() -> None:
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)

    index = summarise_graph_index(graph)
    assert isinstance(index.get("cron"), list)
    mermaid = index.get("mermaid")
    assert isinstance(mermaid, str)
    assert "graph TD" in mermaid


def test_summarise_graph_index_extracts_cron_nodes() -> None:
    graph = StateGraph(State)
    graph.add_node(
        "cron_trigger",
        CronTriggerNode(
            name="cron_trigger",
            expression="*/5 * * * *",
            timezone="UTC",
            allow_overlapping=False,
        ),
    )
    graph.add_edge(START, "cron_trigger")
    graph.add_edge("cron_trigger", END)

    index = summarise_graph_index(graph)
    cron = index.get("cron")
    assert isinstance(cron, list)
    assert len(cron) == 1
    assert cron[0]["expression"] == "*/5 * * * *"
    assert cron[0]["timezone"] == "UTC"
    assert cron[0]["allow_overlapping"] is False


def test_summarise_graph_index_extracts_listener_nodes() -> None:
    graph = StateGraph(State)
    graph.add_node(
        "telegram_listener",
        TelegramBotListenerNode(
            name="telegram_listener",
            token="[[telegram_token]]",
            allowed_updates=["message", "callback_query"],
            allowed_chat_types=["private"],
            poll_timeout_seconds=45,
        ),
    )
    graph.add_edge(START, "telegram_listener")
    graph.add_edge("telegram_listener", END)

    index = summarise_graph_index(graph)
    listeners = index.get("listeners")
    assert isinstance(listeners, list)
    assert len(listeners) == 1
    assert listeners[0]["node_name"] == "telegram_listener"
    assert listeners[0]["platform"] == "telegram"
    assert listeners[0]["allowed_updates"] == ["message", "callback_query"]
    assert listeners[0]["poll_timeout_seconds"] == 45


def test_summarise_graph_index_extracts_qq_listener_nodes() -> None:
    graph = StateGraph(State)
    graph.add_node(
        "qq_listener",
        QQBotListenerNode(
            name="qq_listener",
            app_id="[[qq_app_id]]",
            client_secret="[[qq_client_secret]]",
            allowed_scene_types=["c2c", "group"],
        ),
    )
    graph.add_edge(START, "qq_listener")
    graph.add_edge("qq_listener", END)

    index = summarise_graph_index(graph)
    listeners = index.get("listeners")
    assert isinstance(listeners, list)
    assert len(listeners) == 1
    assert listeners[0]["node_name"] == "qq_listener"
    assert listeners[0]["platform"] == "qq"
    assert listeners[0]["allowed_scene_types"] == ["c2c", "group"]


def test_summarise_graph_index_renders_workflow_tool_subgraph() -> None:
    graph = StateGraph(State)
    agent = AgentNode(
        name="agent",
        ai_model="gpt-4o-mini",
        workflow_tools=[
            WorkflowTool(
                name="tool",
                description="tool desc",
                graph=_build_tool_graph(),
                args_schema=ToolInput,
            )
        ],
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)

    index = summarise_graph_index(graph)

    mermaid = index.get("mermaid")
    assert isinstance(mermaid, str)
    assert "mermaid_compact" not in index
    assert 'subgraph root__agent__tool__tool__subgraph["tool"]' in mermaid
    assert "root__node__agent -.-> root__agent__tool__tool__start;" in mermaid
    assert 'root__start(["START"]):::first' in mermaid
    assert 'root__end(["END"]):::last' in mermaid


def test_extract_cron_index_skips_missing_cron_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing cron fields are skipped when serialising cron index metadata."""
    graph = StateGraph(State)
    graph.add_node("cron_trigger", lambda state: state)

    monkeypatch.setattr(
        summary_module,
        "_serialise_node",
        lambda _name, _runnable: {"type": "CronTriggerNode"},
    )

    cron = summary_module._extract_cron_index(graph)
    assert cron == [{}]


def test_summarise_graph_index_omits_mermaid_key_when_render_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Mermaid rendering fails the index dict has no 'mermaid' key (line 55->57)."""
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)

    monkeypatch.setattr(summary_module, "_render_compact_mermaid", lambda _g: None)
    monkeypatch.setattr(
        summary_module,
        "has_workflow_tool_subgraphs",
        lambda _summary: False,
    )

    index = summary_module.summarise_graph_index(graph)
    assert "mermaid" not in index
