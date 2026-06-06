"""Render Mermaid diagrams from workflow graph payloads."""

from __future__ import annotations
from typing import Any
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT


def render_mermaid_from_graph_payload(graph_payload: dict[str, Any]) -> str | None:
    """Render a Mermaid diagram from a stored workflow graph payload.

    For ``langgraph-script`` payloads the script is executed inside the
    RP-sandboxed loader and LangGraph's native ``draw_mermaid()`` is used.
    Returns ``None`` if the payload does not contain a valid script or
    rendering fails.
    """
    fmt = graph_payload.get("format", "")
    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        return None
    source = graph_payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    return _render_mermaid_from_script(source)


def _render_mermaid_from_script(source: str) -> str | None:
    """Execute ``source`` in the RP sandbox and render a Mermaid diagram."""
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError
    from orcheo.graph.ingestion.loader import load_graph_from_script
    from orcheo.graph.ingestion.summary import _render_compact_mermaid

    try:
        graph = load_graph_from_script(source)
        return _render_compact_mermaid(graph)
    except (ScriptIngestionError, Exception):
        return None


__all__ = [
    "render_mermaid_from_graph_payload",
]
