"""Convert LangGraph StateGraph instances into JSON-serialisable summaries."""

from __future__ import annotations
from typing import Any
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from orcheo.graph.mermaid import (
    has_workflow_tool_subgraphs,
    normalise_mermaid_sentinels,
    render_summary_mermaid,
)
from orcheo.nodes.registry import registry


def summarise_state_graph(graph: StateGraph) -> dict[str, Any]:
    """Return a JSON-serialisable summary of the ``StateGraph`` structure."""
    nodes = [
        _serialise_node(name, spec.runnable, nested_graph_depth=1)
        for name, spec in graph.nodes.items()
    ]
    edges = [_normalise_edge(edge) for edge in sorted(graph.edges)]
    branches = [
        _serialise_branch(source, branch_name, branch)
        for source, branch_map in graph.branches.items()
        for branch_name, branch in branch_map.items()
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "conditional_edges": [
            branch
            for branch in branches
            if branch.get("mapping") or branch.get("default")
        ],
    }


def summarise_graph_index(
    graph: StateGraph,
) -> dict[str, Any]:
    """Return compact graph metadata for downstream CLI/UI consumers."""
    summary = summarise_state_graph(graph)
    index: dict[str, Any] = {
        "cron": _extract_cron_index(graph),
        "listeners": _extract_listener_index(graph),
    }
    compact_mermaid = _render_compact_mermaid(graph)
    mermaid = (
        render_summary_mermaid(summary)
        if has_workflow_tool_subgraphs(summary)
        else compact_mermaid
    )
    if mermaid:
        index["mermaid"] = mermaid
    if compact_mermaid and mermaid and compact_mermaid != mermaid:
        index["mermaid_compact"] = compact_mermaid
    return index


def _serialise_node(
    name: str,
    runnable: Any,
    *,
    nested_graph_depth: int = 0,
) -> dict[str, Any]:
    """Return a JSON representation for a LangGraph node."""
    runnable_obj = _unwrap_runnable(runnable)
    metadata = registry.get_metadata_by_callable(runnable_obj)
    node_type = metadata.name if metadata else type(runnable_obj).__name__
    payload = {"name": name, "type": node_type}

    if isinstance(runnable_obj, BaseModel):
        node_config = runnable_obj.model_dump(
            mode="json",
            fallback=lambda value: _serialise_fallback(
                value,
                nested_graph_depth=nested_graph_depth,
            ),
        )
        node_config.pop("name", None)
        payload.update(node_config)

    return payload


def _serialise_fallback(
    value: Any,
    *,
    nested_graph_depth: int = 0,
) -> Any:
    """Return a JSON-safe representation for unsupported Pydantic values."""
    if isinstance(value, BaseModel):
        return value.model_dump(
            mode="json",
            fallback=lambda nested: _serialise_fallback(
                nested,
                nested_graph_depth=nested_graph_depth,
            ),
        )
    if isinstance(value, StateGraph):
        payload: dict[str, Any] = {
            "type": "StateGraph",
            "nodes": sorted(value.nodes.keys()),
        }
        if nested_graph_depth > 0:
            payload["summary"] = summarise_state_graph_with_depth(
                value,
                nested_graph_depth=nested_graph_depth - 1,
            )
        return payload
    if isinstance(value, CompiledStateGraph):
        return {"type": "CompiledStateGraph"}
    if isinstance(value, type):
        return f"{value.__module__}.{value.__name__}"
    if isinstance(value, set):
        return [
            _serialise_fallback(item, nested_graph_depth=nested_graph_depth)
            for item in value
        ]
    return repr(value)


def summarise_state_graph_with_depth(
    graph: StateGraph,
    *,
    nested_graph_depth: int,
) -> dict[str, Any]:
    """Return a graph summary with a bounded nested graph depth."""
    nodes = [
        _serialise_node(name, spec.runnable, nested_graph_depth=nested_graph_depth)
        for name, spec in graph.nodes.items()
    ]
    edges = [_normalise_edge(edge) for edge in sorted(graph.edges)]
    branches = [
        _serialise_branch(source, branch_name, branch)
        for source, branch_map in graph.branches.items()
        for branch_name, branch in branch_map.items()
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "conditional_edges": [
            branch
            for branch in branches
            if branch.get("mapping") or branch.get("default")
        ],
    }


def _render_compact_mermaid(graph: StateGraph) -> str | None:
    """Return Mermaid text for the top-level graph when rendering succeeds."""
    try:
        return normalise_mermaid_sentinels(graph.compile().get_graph().draw_mermaid())
    except Exception:
        return None


def _extract_cron_index(graph: StateGraph) -> list[dict[str, Any]]:
    """Extract cron trigger fields from ``CronTriggerNode`` graph nodes."""
    cron_nodes: list[dict[str, Any]] = []
    for name, spec in graph.nodes.items():
        node = _serialise_node(name, spec.runnable)
        if node.get("type") != "CronTriggerNode":
            continue

        payload: dict[str, Any] = {}
        for key in (
            "expression",
            "timezone",
            "allow_overlapping",
            "start_at",
            "end_at",
        ):
            if key in node:
                payload[key] = node.get(key)
        cron_nodes.append(payload)
    return cron_nodes


def _extract_listener_index(graph: StateGraph) -> list[dict[str, Any]]:
    """Extract listener node metadata from the graph."""
    from orcheo.nodes.connectors.listener_base import ListenerNode

    listener_nodes: list[dict[str, Any]] = []
    for name, spec in graph.nodes.items():
        runnable_obj = _unwrap_runnable(spec.runnable)
        if not isinstance(runnable_obj, ListenerNode):
            continue
        node = _serialise_node(name, runnable_obj)

        payload: dict[str, Any] = {
            "node_name": name,
            "platform": node.get("platform"),
            "type": node.get("type"),
        }
        for key, value in node.items():
            if key in {"name", "type", "platform"}:
                continue
            payload[key] = value
        listener_nodes.append(payload)
    return listener_nodes


def _unwrap_runnable(runnable: Any) -> Any:
    """Return the underlying callable stored within LangGraph wrappers."""
    if hasattr(runnable, "afunc") and isinstance(runnable.afunc, BaseModel):
        return runnable.afunc
    if hasattr(runnable, "func") and isinstance(runnable.func, BaseModel):
        return runnable.func
    return runnable


def _serialise_branch(source: str, name: str, branch: Any) -> dict[str, Any]:
    """Return metadata describing a conditional branch."""
    mapping: dict[str, str] | None = None
    ends = getattr(branch, "ends", None)
    if isinstance(ends, dict):
        mapping = {str(key): _normalise_vertex(target) for key, target in ends.items()}

    default: str | None = None
    then_target = getattr(branch, "then", None)
    if isinstance(then_target, str):
        default = _normalise_vertex(then_target)

    payload: dict[str, Any] = {
        "source": source,
        "branch": name,
    }
    if mapping:
        payload["mapping"] = mapping
    if default is not None:
        payload["default"] = default
    if hasattr(branch, "path") and getattr(branch.path, "func", None):
        payload["callable"] = getattr(branch.path.func, "__name__", "<lambda>")

    return payload


def _normalise_edge(edge: tuple[str, str]) -> tuple[str, str]:
    """Convert LangGraph sentinel edge names into public constants."""
    source, target = edge
    return (_normalise_vertex(source), _normalise_vertex(target))


def _normalise_vertex(value: str) -> str:
    """Map LangGraph sentinel vertex names to ``START``/``END``."""
    if value == "__start__":
        return "START"
    if value == "__end__":
        return "END"
    return value


__all__ = ["summarise_graph_index", "summarise_state_graph"]
