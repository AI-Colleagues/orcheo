"""Utilities for rendering workflow graphs as Mermaid diagrams."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from orcheo.graph.mermaid import (
    has_workflow_tool_subgraphs,
    normalise_mermaid_sentinels,
    render_summary_mermaid,
)


def _mermaid_from_graph(graph: Mapping[str, Any]) -> str:
    """Render Mermaid definition for the provided workflow graph."""
    if isinstance(graph, Mapping):
        index = graph.get("index")
        if isinstance(index, Mapping):
            mermaid = index.get("mermaid")
            if isinstance(mermaid, str) and mermaid.strip():
                return normalise_mermaid_sentinels(mermaid)
        summary = graph.get("summary")
        if isinstance(summary, Mapping):
            if has_workflow_tool_subgraphs(summary) or summary.get("conditional_edges"):
                return render_summary_mermaid(summary)
            return _compiled_mermaid(summary)
    return _compiled_mermaid(graph)


def _compiled_mermaid(graph: Mapping[str, Any]) -> str:
    """Render a Mermaid diagram from a lightweight graph summary."""
    from langgraph.errors import InvalidUpdateError
    from langgraph.graph import END, START, StateGraph

    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    conditional_edges = list(graph.get("conditional_edges", []))
    has_conditional_edges = bool(conditional_edges)

    node_names = _collect_node_names(nodes)
    normalised_edges = _collect_edges(edges, node_names)
    normalised_edges.extend(_collect_conditional_edges(conditional_edges, node_names))
    normalised_edges = _deduplicate_edges(normalised_edges)

    stub: StateGraph[Any] = StateGraph(dict)  # type: ignore[type-var]
    for name in sorted(node_names):
        stub.add_node(name, _identity_state)  # type: ignore[type-var]

    compiled_edges: list[tuple[Any, Any]] = []
    for source, target in normalised_edges:
        compiled_edges.append(
            (
                _normalise_vertex(source, START, END),
                _normalise_vertex(target, START, END),
            )
        )
    compiled_edges = _ensure_entry_edges(compiled_edges, node_names, START, END)

    for source, target in compiled_edges:
        stub.add_edge(source, target)

    if has_conditional_edges:
        # LangGraph can collapse branch exits in cyclic graphs (for example,
        # while-loops) when rendering Mermaid. Use deterministic fallback so
        # every conditional route in the summary is represented.
        return _render_mermaid_fallback(compiled_edges, node_names)

    compiled = stub.compile()
    try:
        return normalise_mermaid_sentinels(compiled.get_graph().draw_mermaid())
    except InvalidUpdateError:
        # Parallel branches can fan out from START and trigger concurrent
        # root-state writes during LangGraph rendering.
        return _render_mermaid_fallback(compiled_edges, node_names)


def _identity_state(state: dict[str, Any], *_: Any, **__: Any) -> dict[str, Any]:
    return state


def _ensure_entry_edges(
    edges: list[tuple[Any, Any]],
    node_names: set[str],
    start: Any,
    end: Any,
) -> list[tuple[Any, Any]]:
    if not edges:
        if node_names:
            return [(start, sorted(node_names)[0])]
        return [(start, end)]

    if any(source is start for source, _ in edges):
        return edges

    targets = {target for _, target in edges}
    for candidate in sorted(node_names):
        if candidate not in targets:
            return [*edges, (start, candidate)]
    return [*edges, (start, edges[0][0])]


def _collect_node_names(nodes: Sequence[Any]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        identifier = _node_identifier(node)
        if not identifier:
            continue
        if identifier.upper() in {"START", "END"}:
            continue
        names.add(identifier)
    return names


def _collect_edges(edges: Sequence[Any], node_names: set[str]) -> list[tuple[Any, Any]]:
    pairs: list[tuple[Any, Any]] = []
    for edge in edges:
        resolved = _resolve_edge(edge)
        if not resolved:
            continue
        source, target = resolved
        pairs.append((source, target))
        _register_endpoint(node_names, source)
        _register_endpoint(node_names, target)
    return pairs


def _collect_conditional_edges(
    branches: Sequence[Any], node_names: set[str]
) -> list[tuple[Any, Any]]:
    pairs: list[tuple[Any, Any]] = []
    for branch in branches:
        if not isinstance(branch, Mapping):
            continue

        source = branch.get("source") or branch.get("from")
        if not source:
            continue

        targets: list[Any] = []
        mapping = branch.get("mapping")
        if isinstance(mapping, Mapping):
            targets.extend(mapping.values())
        default_target = branch.get("default") or branch.get("then")
        if default_target:
            targets.append(default_target)

        for target in targets:
            resolved = _resolve_edge({"from": source, "to": target})
            if not resolved:
                continue
            pair_source, pair_target = resolved
            pairs.append((pair_source, pair_target))
            _register_endpoint(node_names, pair_source)
            _register_endpoint(node_names, pair_target)
    return pairs


def _deduplicate_edges(edges: Sequence[tuple[Any, Any]]) -> list[tuple[Any, Any]]:
    unique: list[tuple[Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source, target in edges:
        key = (str(source), str(target))
        if key in seen:
            continue
        seen.add(key)
        unique.append((source, target))
    return unique


def _node_identifier(node: Any) -> str | None:
    if isinstance(node, Mapping):
        raw = (
            node.get("id") or node.get("name") or node.get("label") or node.get("type")
        )
        if raw is None:
            return None
        return str(raw)
    if node is None:
        return None
    return str(node)


def _resolve_edge(edge: Any) -> tuple[Any, Any] | None:
    if isinstance(edge, Mapping):
        source = edge.get("from") or edge.get("source")
        target = edge.get("to") or edge.get("target")
    elif isinstance(edge, Sequence):
        if isinstance(edge, (str, bytes)):  # noqa: UP038
            return None
        if len(edge) != 2:
            return None
        source, target = edge
    else:
        return None
    if not source or not target:
        return None
    return source, target


def _register_endpoint(node_names: set[str], endpoint: Any) -> None:
    text = str(endpoint)
    if text.upper() in {"START", "END"}:
        return
    node_names.add(text)


def _normalise_vertex(value: Any, start: Any, end: Any) -> Any:
    text = str(value)
    upper = text.upper()
    if upper == "START":
        return start
    if upper == "END":
        return end
    return text


def _render_mermaid_fallback(
    edges: Sequence[tuple[Any, Any]],
    node_names: set[str],
) -> str:
    lines = [
        "---",
        "config:",
        "  flowchart:",
        "    curve: linear",
        "---",
        "graph TD;",
        "\t__start__([<p>__start__</p>]):::first",
    ]
    referenced_nodes: set[str] = set()
    edge_lines: list[str] = []

    for source, target in edges:
        source_text = str(source)
        target_text = str(target)
        edge_lines.append(f"\t{source_text} --> {target_text};")
        referenced_nodes.add(source_text)
        referenced_nodes.add(target_text)

    fallback_nodes = sorted(
        {
            node
            for node in {*(str(value) for value in node_names), *referenced_nodes}
            if node not in {"__start__", "__end__"}
        }
    )
    for node in fallback_nodes:
        if node in {"__start__", "__end__"}:
            continue
        lines.append(f"\t{node}({node})")

    lines.append("\t__end__([<p>__end__</p>]):::last")
    lines.extend(edge_lines)
    lines.append("\tclassDef default fill:#f2f0ff,line-height:1.2")
    lines.append("\tclassDef first fill:#bfb6fc")
    lines.append("\tclassDef last fill:#bfb6fc")
    return normalise_mermaid_sentinels("\n".join(lines))


__all__ = [
    "_mermaid_from_graph",
    "_compiled_mermaid",
    "_collect_node_names",
    "_collect_edges",
    "_collect_conditional_edges",
    "_node_identifier",
    "_resolve_edge",
    "_register_endpoint",
    "_normalise_vertex",
    "_render_mermaid_fallback",
    "_deduplicate_edges",
    "_identity_state",
]
