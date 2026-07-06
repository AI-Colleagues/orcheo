"""Utilities for rendering workflow summaries as Mermaid diagrams."""

from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from typing import Any


_MERMAID_ID_RE = re.compile(r"[^0-9A-Za-z_]")


def has_workflow_tool_subgraphs(summary: Mapping[str, Any]) -> bool:
    """Return whether the summary contains expandable nested graphs.

    True when any node is an inlined sub-graph (its runnable is a compiled
    ``StateGraph``) or carries a workflow tool whose graph has a nested summary.
    Either case means the summary-based renderer can draw expanded subgraphs
    instead of one opaque node.
    """
    for node in _mapping_sequence(summary.get("nodes")):
        if _node_subgraph_summary(node) is not None:
            return True
        for tool in _mapping_sequence(node.get("workflow_tools")):
            graph = tool.get("graph")
            if not isinstance(graph, Mapping):
                continue
            nested_summary = graph.get("summary")
            if isinstance(nested_summary, Mapping):
                return True
    return False


def _node_subgraph_summary(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the nested summary for an inlined sub-graph node, if present."""
    graph = node.get("graph")
    if not isinstance(graph, Mapping):
        return None
    nested_summary = graph.get("summary")
    return nested_summary if isinstance(nested_summary, Mapping) else None


def render_summary_mermaid(summary: Mapping[str, Any]) -> str:
    """Render a workflow summary, including tool subgraphs, as Mermaid."""
    body_lines = _render_graph_section(
        summary,
        prefix="root",
        indent="\t",
        root=True,
    )
    lines = [
        "---",
        "config:",
        "  flowchart:",
        "    curve: linear",
        "---",
        "graph TD;",
        *body_lines,
        "\tclassDef default fill:#f2f0ff,line-height:1.2",
        "\tclassDef first fill-opacity:0",
        "\tclassDef last fill:#bfb6fc",
        "\tclassDef tool fill:#e8f6ef,stroke:#4d8f6a,line-height:1.2",
        "\tclassDef toolBoundary fill:#f6faf8,stroke:#8ab79c,stroke-dasharray: 4 4",
    ]
    return "\n".join(lines)


def normalise_mermaid_sentinels(mermaid: str) -> str:
    """Replace Mermaid sentinel labels with the public START/END labels."""
    return (
        mermaid.replace("([<p>__start__</p>])", "([<p>START</p>])")
        .replace("([<p>__end__</p>])", "([<p>END</p>])")
        .replace("(__start__)", "(START)")
        .replace("(__end__)", "(END)")
    )


def _render_graph_section(
    summary: Mapping[str, Any],
    *,
    prefix: str,
    indent: str,
    root: bool,
) -> list[str]:
    node_map = _node_map(summary)
    node_names = _collect_node_names(summary, node_map)
    edges = _ensure_entry_edge_specs(_collect_edge_specs(summary), node_names)

    # Sub-graph nodes are inlined *in place*: the expanded box replaces the plain
    # node box and the outer edges connect to it directly (keyed by node name ->
    # subgraph id), rather than dangling off a dotted line like workflow tools.
    subgraph_ids = {
        name: _mermaid_id(f"{prefix}__{name}__subgraph__subgraph")
        for name, node in node_map.items()
        if _node_subgraph_summary(node) is not None
    }

    start_id = _sentinel_id(prefix, "start")
    end_id = _sentinel_id(prefix, "end")
    lines: list[str] = []
    if not root:
        # Lay nested subgraphs out left-to-right while the outer graph stays
        # top-down, so an inlined pipeline reads as a horizontal lane.
        lines.append(f"{indent}direction LR")
    lines.append(
        _terminal_node_line(
            start_id,
            "START",
            "first" if root else "toolBoundary",
            indent,
        )
    )

    for name in sorted(node_names):
        if name in subgraph_ids:
            continue
        node_id = _node_id(prefix, name)
        node_class = "tool" if not root else None
        lines.append(_node_line(node_id, name, indent, node_class=node_class))

    for name, node in sorted(node_map.items()):
        nested_summary = _node_subgraph_summary(node)
        if nested_summary is not None:
            sub_prefix = f"{prefix}__{name}__subgraph"
            subgraph_id = subgraph_ids[name]
            lines.append(f'{indent}subgraph {subgraph_id}["{_escape_label(name)}"]')
            lines.extend(
                _render_graph_section(
                    nested_summary,
                    prefix=sub_prefix,
                    indent=f"{indent}\t",
                    root=False,
                )
            )
            lines.append(f"{indent}end")

        for tool in _mapping_sequence(node.get("workflow_tools")):
            graph = tool.get("graph")
            if not isinstance(graph, Mapping):
                continue
            nested_summary = graph.get("summary")
            if not isinstance(nested_summary, Mapping):
                continue

            tool_name = str(tool.get("name") or "workflow_tool")
            sub_prefix = f"{prefix}__{name}__tool__{tool_name}"
            subgraph_id = _mermaid_id(f"{sub_prefix}__subgraph")
            lines.append(
                f'{indent}subgraph {subgraph_id}["{_escape_label(tool_name)}"]'
            )
            lines.extend(
                _render_graph_section(
                    nested_summary,
                    prefix=sub_prefix,
                    indent=f"{indent}\t",
                    root=False,
                )
            )
            lines.append(f"{indent}end")
            tool_start_id = _sentinel_id(sub_prefix, "start")
            lines.append(f"{indent}{_node_id(prefix, name)} -.-> {tool_start_id};")

    lines.append(
        _terminal_node_line(
            end_id,
            "END",
            "last" if root else "toolBoundary",
            indent,
        )
    )

    for source, target, label, conditional in edges:
        lines.append(
            _edge_line(
                _vertex_id(prefix, source, start_id, end_id, subgraph_ids),
                _vertex_id(prefix, target, start_id, end_id, subgraph_ids),
                indent,
                label=label,
                conditional=conditional,
            )
        )
    return lines


def _mapping_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _node_map(summary: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    node_map: dict[str, Mapping[str, Any]] = {}
    for node in _mapping_sequence(summary.get("nodes")):
        name = node.get("name") or node.get("id") or node.get("label")
        if name is None:
            continue
        node_map[str(name)] = node
    return node_map


def _collect_node_names(
    summary: Mapping[str, Any],
    node_map: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    names = set(node_map.keys())
    for source, target in _collect_edges(summary):
        if source.upper() != "START":
            names.add(source)
        if target.upper() != "END":
            names.add(target)
    return names


def _collect_edges(summary: Mapping[str, Any]) -> list[tuple[str, str]]:
    edges = [
        (source, target)
        for source, target, _label, _conditional in _collect_edge_specs(summary)
    ]
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if edge in seen:
            continue
        seen.add(edge)
        deduped.append(edge)
    return deduped


def _collect_edge_specs(
    summary: Mapping[str, Any],
) -> list[tuple[str, str, str | None, bool]]:
    edge_specs: list[tuple[str, str, str | None, bool]] = []
    for edge in _sequence(summary.get("edges")):
        resolved = _resolve_edge(edge)
        if resolved is None:
            continue
        source, target = resolved
        edge_specs.append((source, target, None, False))

    for branch in _mapping_sequence(summary.get("conditional_edges")):
        edge_specs.extend(_branch_edge_specs(branch))

    deduped: list[tuple[str, str, str | None, bool]] = []
    seen: set[tuple[str, str, str | None, bool]] = set()
    for edge_spec in edge_specs:
        if edge_spec in seen:
            continue
        seen.add(edge_spec)
        deduped.append(edge_spec)
    return deduped


def _branch_edge_specs(
    branch: Mapping[str, Any],
) -> list[tuple[str, str, str | None, bool]]:
    source = branch.get("source") or branch.get("from")
    if source is None:
        return []

    edge_specs: list[tuple[str, str, str | None, bool]] = []
    mapping = branch.get("mapping")
    if isinstance(mapping, Mapping):
        for label, target in mapping.items():
            resolved = _resolve_edge({"source": source, "target": target})
            if resolved is None:
                continue
            edge_specs.append((resolved[0], resolved[1], str(label), True))

    default_target = branch.get("default") or branch.get("then")
    if default_target is not None:
        resolved = _resolve_edge({"source": source, "target": default_target})
        if resolved is not None:  # pragma: no branch - source/target are non-None here
            edge_specs.append((resolved[0], resolved[1], "default", True))

    return edge_specs


def _branch_targets(branch: Mapping[str, Any]) -> list[str]:
    targets: list[str] = []
    mapping = branch.get("mapping")
    if isinstance(mapping, Mapping):  # pragma: no branch
        targets.extend(str(target) for target in mapping.values())

    default_target = branch.get("default") or branch.get("then")
    if default_target is not None:  # pragma: no branch
        targets.append(str(default_target))
    return targets


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return list(value)


def _resolve_edge(edge: Any) -> tuple[str, str] | None:
    if isinstance(edge, Mapping):
        source = edge.get("source") or edge.get("from")
        target = edge.get("target") or edge.get("to")
    elif isinstance(edge, Sequence) and not isinstance(edge, str | bytes):
        if len(edge) != 2:
            return None
        source, target = edge
    else:
        return None

    if source is None or target is None:
        return None

    source_text = str(source)
    target_text = str(target)
    if not source_text or not target_text:
        return None  # pragma: no cover
    return source_text, target_text


def _ensure_entry_edges(
    edges: list[tuple[str, str]],
    node_names: set[str],
) -> list[tuple[str, str]]:
    if not edges:
        if node_names:
            return [("START", sorted(node_names)[0])]
        return [("START", "END")]

    if any(source.upper() == "START" for source, _ in edges):
        return edges

    targets = {target for _, target in edges}
    for candidate in sorted(node_names):
        if candidate not in targets:
            return [*edges, ("START", candidate)]
    return [*edges, ("START", edges[0][0])]


def _ensure_entry_edge_specs(
    edges: list[tuple[str, str, str | None, bool]],
    node_names: set[str],
) -> list[tuple[str, str, str | None, bool]]:
    if not edges:
        if node_names:
            return [("START", sorted(node_names)[0], None, False)]
        return [("START", "END", None, False)]

    if any(source.upper() == "START" for source, _, _, _ in edges):
        return edges

    targets = {target for _, target, _, _ in edges}
    for candidate in sorted(node_names):
        if candidate not in targets:
            return [*edges, ("START", candidate, None, False)]
    return [*edges, ("START", edges[0][0], None, False)]


def _vertex_id(
    prefix: str,
    value: str,
    start_id: str,
    end_id: str,
    subgraph_ids: Mapping[str, str] | None = None,
) -> str:
    upper = value.upper()
    if upper == "START":
        return start_id
    if upper == "END":
        return end_id
    if subgraph_ids and value in subgraph_ids:
        return subgraph_ids[value]
    return _node_id(prefix, value)


def _node_id(prefix: str, name: str) -> str:
    return _mermaid_id(f"{prefix}__node__{name}")


def _sentinel_id(prefix: str, name: str) -> str:
    return _mermaid_id(f"{prefix}__{name}")


def _mermaid_id(value: str) -> str:
    cleaned = _MERMAID_ID_RE.sub("_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned


def _node_line(
    node_id: str,
    label: str,
    indent: str,
    *,
    node_class: str | None = None,
) -> str:
    class_suffix = f":::{node_class}" if node_class else ""
    return f'{indent}{node_id}["{_escape_label(label)}"]{class_suffix}'


def _terminal_node_line(node_id: str, label: str, node_class: str, indent: str) -> str:
    return f'{indent}{node_id}(["{_escape_label(label)}"]):::{node_class}'


def _edge_line(
    source_id: str,
    target_id: str,
    indent: str,
    *,
    label: str | None,
    conditional: bool,
) -> str:
    if conditional:
        if label:
            return (
                f"{indent}{source_id} -. {_escape_edge_label(label)} .-> {target_id};"
            )
        return f"{indent}{source_id} -.-> {target_id};"
    return f"{indent}{source_id} --> {target_id};"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_edge_label(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = [
    "has_workflow_tool_subgraphs",
    "normalise_mermaid_sentinels",
    "render_summary_mermaid",
]
