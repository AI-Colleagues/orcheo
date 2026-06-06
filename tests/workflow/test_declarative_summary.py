"""Tests for declarative graph summary derivation."""

from __future__ import annotations
from orcheo.workflow.declarative_summary import (
    DECLARATIVE_GRAPH_FORMAT,
    derive_declarative_graph_index,
    ingest_declarative_graph,
    summarise_declarative_graph,
)
from orcheo.workflow.trust.schema import (
    DeclarativeConditionalEdgeDef,
    DeclarativeEdgeDef,
    DeclarativeListenerDef,
    DeclarativeNodeDef,
    DeclarativeTriggerDef,
    DeclarativeWorkflowGraph,
)


def _simple_graph() -> DeclarativeWorkflowGraph:
    return DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="fetch", type="RSSNode", config={"url": "http://a.io"}
            ),
            DeclarativeNodeDef(id="send", type="SlackNode"),
        ],
        edges=[
            DeclarativeEdgeDef(source="START", target="fetch"),
            DeclarativeEdgeDef(source="fetch", target="send"),
        ],
        conditional_edges=[
            DeclarativeConditionalEdgeDef(
                source="fetch",
                branch="route",
                mapping={"ok": "send", "fail": "END"},
                default="END",
            )
        ],
    )


def test_summarise_declarative_graph_nodes() -> None:
    graph = _simple_graph()
    summary = summarise_declarative_graph(graph)

    assert len(summary["nodes"]) == 2
    node_names = {n["name"] for n in summary["nodes"]}
    assert "fetch" in node_names
    assert "send" in node_names


def test_summarise_node_includes_type_and_config() -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="fetch", type="RSSNode", config={"url": "http://a.io"}
            )
        ]
    )
    summary = summarise_declarative_graph(graph)

    node = summary["nodes"][0]
    assert node["type"] == "RSSNode"
    assert node["url"] == "http://a.io"


def test_summarise_declarative_graph_edges() -> None:
    graph = _simple_graph()
    summary = summarise_declarative_graph(graph)

    assert ("START", "fetch") in summary["edges"]
    assert ("fetch", "send") in summary["edges"]


def test_summarise_conditional_edge_with_mapping_and_default() -> None:
    graph = _simple_graph()
    summary = summarise_declarative_graph(graph)

    ce = summary["conditional_edges"][0]
    assert ce["source"] == "fetch"
    assert ce["branch"] == "route"
    assert ce["mapping"] == {"ok": "send", "fail": "END"}
    assert ce["default"] == "END"


def test_summarise_conditional_edge_without_optional_fields() -> None:
    graph = DeclarativeWorkflowGraph(
        conditional_edges=[
            DeclarativeConditionalEdgeDef(
                source="node_a", branch="branch_x", mapping={}
            )
        ]
    )
    summary = summarise_declarative_graph(graph)

    ce = summary["conditional_edges"][0]
    assert ce["source"] == "node_a"
    assert "mapping" not in ce
    assert "default" not in ce


def test_derive_declarative_graph_index_cron_from_trigger() -> None:
    graph = DeclarativeWorkflowGraph(
        triggers=[
            DeclarativeTriggerDef(
                type="CronTriggerNode",
                config={"expression": "0 * * * *", "timezone": "UTC"},
            ),
            DeclarativeTriggerDef(type="ManualTriggerNode", config={}),
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert len(index["cron"]) == 1
    assert index["cron"][0]["expression"] == "0 * * * *"
    assert index["cron"][0]["timezone"] == "UTC"


def test_derive_declarative_graph_index_cron_from_node() -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="cron1",
                type="CronTriggerNode",
                config={"expression": "5 4 * * *"},
            )
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert len(index["cron"]) == 1
    assert index["cron"][0]["expression"] == "5 4 * * *"


def test_derive_declarative_graph_index_cron_node_no_known_keys() -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="cron1",
                type="CronTriggerNode",
                config={"unknown_key": "value"},
            )
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert index["cron"] == []


def test_derive_declarative_graph_index_listeners_from_listener_def() -> None:
    graph = DeclarativeWorkflowGraph(
        listeners=[
            DeclarativeListenerDef(
                type="TelegramBotListenerNode",
                config={"token": "abc123"},
            )
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert len(index["listeners"]) == 1
    listener = index["listeners"][0]
    assert listener["type"] == "TelegramBotListenerNode"
    assert listener["token"] == "abc123"


def test_derive_declarative_graph_index_listeners_from_node() -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="tg_listener",
                type="TelegramBotListenerNode",
                config={"bot_id": "bot-1"},
            )
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert len(index["listeners"]) == 1
    assert index["listeners"][0]["node_name"] == "tg_listener"
    assert index["listeners"][0]["type"] == "TelegramBotListenerNode"


def test_derive_declarative_graph_index_listeners_from_all_supported_types() -> None:
    listener_types = [
        "DiscordBotListenerNode",
        "QQBotListenerNode",
        "WebhookTriggerNode",
        "HttpPollingTriggerNode",
        "SlackEventsParserNode",
        "WeComEventsParserNode",
        "TelegramEventsParserNode",
        "DiscordEventsParserNode",
    ]
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(id=f"n_{i}", type=t)
            for i, t in enumerate(listener_types)
        ]
    )
    index = derive_declarative_graph_index(graph)

    assert len(index["listeners"]) == len(listener_types)


def test_ingest_declarative_graph_returns_complete_payload() -> None:
    graph = _simple_graph()
    result = ingest_declarative_graph(graph)

    assert result["format"] == DECLARATIVE_GRAPH_FORMAT
    assert result["version"] == 1
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 2
    assert len(result["conditional_edges"]) == 1
    assert "summary" in result
    assert "index" in result
    assert "nodes" in result["summary"]
    assert "edges" in result["summary"]
    assert "conditional_edges" in result["summary"]


def test_ingest_declarative_graph_empty() -> None:
    graph = DeclarativeWorkflowGraph()
    result = ingest_declarative_graph(graph)

    assert result["format"] == DECLARATIVE_GRAPH_FORMAT
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["conditional_edges"] == []
    assert result["index"]["cron"] == []
    assert result["index"]["listeners"] == []
