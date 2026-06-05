"""Derive ingestion summary metadata from declarative workflow graphs."""

from __future__ import annotations
from typing import Any
from orcheo.workflow.trust.schema import (
    DeclarativeConditionalEdgeDef,
    DeclarativeEdgeDef,
    DeclarativeNodeDef,
    DeclarativeWorkflowGraph,
)


DECLARATIVE_GRAPH_FORMAT = "orcheo-declarative-graph"


def summarise_declarative_graph(graph: DeclarativeWorkflowGraph) -> dict[str, Any]:
    """Return a JSON-serialisable summary derived from a declarative graph payload."""
    nodes = [_summarise_node(node) for node in graph.nodes]
    edges = [_summarise_edge(edge) for edge in graph.edges]
    conditional_edges = [
        _summarise_conditional_edge(ce) for ce in graph.conditional_edges
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "conditional_edges": conditional_edges,
    }


def derive_declarative_graph_index(graph: DeclarativeWorkflowGraph) -> dict[str, Any]:
    """Return compact graph metadata index derived from a declarative graph payload."""
    cron = _extract_cron_index(graph)
    listeners = _extract_listener_index(graph)
    index: dict[str, Any] = {
        "cron": cron,
        "listeners": listeners,
    }
    return index


def ingest_declarative_graph(
    graph: DeclarativeWorkflowGraph,
) -> dict[str, Any]:
    """Return a workflow graph payload from a declarative graph definition.

    Never executes Python or imports any tenant code.
    """
    summary = summarise_declarative_graph(graph)
    index = derive_declarative_graph_index(graph)
    return {
        "format": DECLARATIVE_GRAPH_FORMAT,
        "version": graph.version,
        "nodes": [node.model_dump() for node in graph.nodes],
        "edges": [edge.model_dump() for edge in graph.edges],
        "conditional_edges": [ce.model_dump() for ce in graph.conditional_edges],
        "triggers": [t.model_dump() for t in graph.triggers],
        "listeners": [ll.model_dump() for ll in graph.listeners],
        "credential_references": [c.model_dump() for c in graph.credential_references],
        "metadata": graph.metadata,
        "summary": summary,
        "index": index,
    }


def _summarise_node(node: DeclarativeNodeDef) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": node.id, "type": node.type}
    payload.update(node.config)
    return payload


def _summarise_edge(edge: DeclarativeEdgeDef) -> tuple[str, str]:
    return (edge.source, edge.target)


def _summarise_conditional_edge(
    ce: DeclarativeConditionalEdgeDef,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": ce.source,
        "branch": ce.branch,
    }
    if ce.mapping:
        payload["mapping"] = ce.mapping
    if ce.default is not None:
        payload["default"] = ce.default
    return payload


def _extract_cron_index(graph: DeclarativeWorkflowGraph) -> list[dict[str, Any]]:
    """Extract cron trigger metadata from declarative trigger definitions."""
    cron_nodes: list[dict[str, Any]] = []
    for trigger in graph.triggers:
        if trigger.type != "CronTriggerNode":
            continue
        payload: dict[str, Any] = {}
        for key in (
            "expression",
            "timezone",
            "allow_overlapping",
            "start_at",
            "end_at",
        ):
            if key in trigger.config:
                payload[key] = trigger.config[key]
        cron_nodes.append(payload)
    # Also check nodes for CronTriggerNode
    for node in graph.nodes:
        if node.type != "CronTriggerNode":
            continue
        payload = {}
        for key in (
            "expression",
            "timezone",
            "allow_overlapping",
            "start_at",
            "end_at",
        ):
            if key in node.config:
                payload[key] = node.config[key]
        if payload:
            cron_nodes.append(payload)
    return cron_nodes


def _extract_listener_index(graph: DeclarativeWorkflowGraph) -> list[dict[str, Any]]:
    """Extract listener metadata from declarative listener definitions."""
    listener_nodes: list[dict[str, Any]] = []
    for listener in graph.listeners:
        payload: dict[str, Any] = {
            "type": listener.type,
        }
        payload.update(listener.config)
        listener_nodes.append(payload)
    # Also check nodes for listener-type nodes
    listener_node_types = {
        "TelegramBotListenerNode",
        "DiscordBotListenerNode",
        "QQBotListenerNode",
        "WebhookTriggerNode",
        "HttpPollingTriggerNode",
        "SlackEventsParserNode",
        "WeComEventsParserNode",
        "TelegramEventsParserNode",
        "DiscordEventsParserNode",
    }
    for node in graph.nodes:
        if node.type not in listener_node_types:
            continue
        payload = {"node_name": node.id, "type": node.type}
        payload.update(node.config)
        listener_nodes.append(payload)
    return listener_nodes


__all__ = [
    "DECLARATIVE_GRAPH_FORMAT",
    "derive_declarative_graph_index",
    "ingest_declarative_graph",
    "summarise_declarative_graph",
]
