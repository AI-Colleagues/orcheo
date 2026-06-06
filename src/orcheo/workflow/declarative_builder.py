"""Build LangGraph StateGraph instances from declarative workflow graph payloads."""

from __future__ import annotations
import logging
from typing import Any
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.registry import registry


logger = logging.getLogger(__name__)

DECLARATIVE_GRAPH_FORMAT = "orcheo-declarative-graph"


class DeclarativeGraphBuildError(ValueError):
    """Raised when a declarative graph payload cannot be built into a StateGraph."""


def build_graph_from_declarative(graph_payload: dict[str, Any]) -> StateGraph:
    """Build a LangGraph StateGraph from a declarative graph payload dict.

    Raises ``DeclarativeGraphBuildError`` if any node type is unknown or the
    payload is not in declarative format.
    """
    fmt = graph_payload.get("format", "")
    if fmt != DECLARATIVE_GRAPH_FORMAT:
        msg = (
            f"Expected declarative graph format"
            f" '{DECLARATIVE_GRAPH_FORMAT}', got '{fmt}'."
        )
        raise DeclarativeGraphBuildError(msg)

    graph = StateGraph(State)

    for node_def in graph_payload.get("nodes", []):
        node_id = node_def.get("id") or node_def.get("name", "")
        node_type = node_def.get("type", "")
        node_config = dict(node_def.get("config", {}))

        node_class = registry.get_node(node_type)
        if node_class is None:
            msg = f"Unknown node type '{node_type}' in declarative graph."
            raise DeclarativeGraphBuildError(msg)

        if "name" not in node_config:
            node_config["name"] = node_id
        node_instance = node_class(**node_config)
        graph.add_node(node_id, node_instance)
        logger.debug("Added declarative node %s (%s)", node_id, node_type)

    for edge_def in graph_payload.get("edges", []):
        source = edge_def.get("source", "")
        target = edge_def.get("target", "")
        src = START if source == "START" else source
        tgt = END if target == "END" else target
        graph.add_edge(src, tgt)

    for ce_def in graph_payload.get("conditional_edges", []):
        source = ce_def.get("source", "")
        branch_name = ce_def.get("branch", "")
        mapping = ce_def.get("mapping", {})
        default = ce_def.get("default")
        resolved_mapping = {
            key: (END if val == "END" else val) for key, val in mapping.items()
        }
        if default:
            resolved_mapping["__default__"] = END if default == "END" else default
        if resolved_mapping:
            graph.add_conditional_edges(source, branch_name, resolved_mapping)

    return graph


def is_declarative_graph_payload(graph_payload: dict[str, Any]) -> bool:
    """Return True when the payload is a declarative graph."""
    return graph_payload.get("format") == DECLARATIVE_GRAPH_FORMAT


__all__ = [
    "DeclarativeGraphBuildError",
    "DECLARATIVE_GRAPH_FORMAT",
    "build_graph_from_declarative",
    "is_declarative_graph_payload",
]
