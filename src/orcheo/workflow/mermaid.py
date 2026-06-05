"""Render Mermaid diagrams from declarative workflow graph summaries."""

from __future__ import annotations
from typing import Any


def _render_nodes(nodes: list[dict[str, Any]], lines: list[str]) -> None:
    """Append Mermaid node declarations to lines."""
    seen: set[str] = set()
    for node in nodes:
        name = node.get("name") or node.get("id", "")
        node_type = node.get("type", "")
        if name and name not in seen:
            label = f"{name}\\n[{node_type}]" if node_type else name
            lines.append(f'    {name}["{label}"]')
            seen.add(name)


def _render_edges(edges: list, lines: list[str]) -> None:
    """Append Mermaid edge declarations to lines."""
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) == 2:
            src, tgt = edge
        elif isinstance(edge, dict):
            src = edge.get("source", "")
            tgt = edge.get("target", "")
        else:
            continue
        if src and tgt:
            lines.append(f"    {src} --> {tgt}")


def _render_conditional_edges(
    conditional_edges: list[dict[str, Any]], lines: list[str]
) -> None:
    """Append Mermaid conditional edge declarations to lines."""
    for ce in conditional_edges:
        src = ce.get("source", "")
        mapping = ce.get("mapping", {})
        for key, tgt in mapping.items():
            if src and tgt:
                lines.append(f"    {src} -->|{key}| {tgt}")
        default = ce.get("default")
        if src and default:
            lines.append(f"    {src} -->|default| {default}")


def render_declarative_mermaid(summary: dict[str, Any]) -> str:
    """Render a Mermaid flowchart from a declarative graph summary dict."""
    lines: list[str] = ["graph TD"]
    _render_nodes(summary.get("nodes", []), lines)
    _render_edges(summary.get("edges", []), lines)
    _render_conditional_edges(summary.get("conditional_edges", []), lines)
    return "\n".join(lines)


def render_mermaid_from_graph_payload(graph_payload: dict[str, Any]) -> str | None:
    """Render Mermaid from a stored workflow graph payload.

    Returns None if the payload does not contain a summary or is not a
    declarative graph.
    """
    fmt = graph_payload.get("format", "")
    if fmt != "orcheo-declarative-graph":
        return None
    summary = graph_payload.get("summary")
    if not isinstance(summary, dict):
        return None
    return render_declarative_mermaid(summary)


__all__ = [
    "render_declarative_mermaid",
    "render_mermaid_from_graph_payload",
]
