"""Render Mermaid diagrams from workflow graph payloads."""

from __future__ import annotations
import logging
from typing import Any
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT


logger = logging.getLogger(__name__)


def render_mermaid_from_graph_payload(graph_payload: dict[str, Any]) -> str | None:
    """Render a Mermaid diagram from a stored workflow graph payload.

    For ``langgraph-script`` payloads the script is executed and LangGraph's
    native ``draw_mermaid()`` is used.  Returns ``None`` if the payload does
    not contain a valid script or rendering fails.
    """
    fmt = graph_payload.get("format", "")
    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        return None
    source = graph_payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    entrypoint = graph_payload.get("entrypoint")
    return _render_mermaid_from_script(source, entrypoint)


def _render_mermaid_from_script(
    source: str, entrypoint: str | None = None
) -> str | None:
    """Execute ``source`` and render a Mermaid diagram from the graph."""
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError
    from orcheo.graph.ingestion.loader import load_graph_from_script
    from orcheo.graph.ingestion.summary import (
        _render_compact_mermaid,
        summarise_state_graph,
    )
    from orcheo.graph.mermaid import has_workflow_tool_subgraphs, render_summary_mermaid

    try:
        graph = load_graph_from_script(source, entrypoint=entrypoint)
        summary = summarise_state_graph(graph)
        if has_workflow_tool_subgraphs(summary):
            return render_summary_mermaid(summary)
        return _render_compact_mermaid(graph)
    except ScriptIngestionError as exc:
        logger.warning("Mermaid rendering skipped: script ingestion error: %s", exc)
        return None
    except Exception:
        logger.warning("Mermaid rendering failed", exc_info=True)
        return None


def _render_mermaid_from_script_full_env(
    source: str, entrypoint: str | None = None
) -> str | None:
    """Render a Mermaid diagram using the full Python environment.

    Mirrors :func:`_render_mermaid_from_script` but uses
    :func:`~orcheo.graph.ingestion.loader.load_graph_from_script_full_env`
    which skips the script size limit.  Only call this from trusted
    server-side contexts where the script has already been size-validated.
    """
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError
    from orcheo.graph.ingestion.loader import load_graph_from_script_full_env
    from orcheo.graph.ingestion.summary import (
        _render_compact_mermaid,
        summarise_state_graph,
    )
    from orcheo.graph.mermaid import has_workflow_tool_subgraphs, render_summary_mermaid

    try:
        graph = load_graph_from_script_full_env(source, entrypoint=entrypoint)
        summary = summarise_state_graph(graph)
        if has_workflow_tool_subgraphs(summary):
            return render_summary_mermaid(summary)
        return _render_compact_mermaid(graph)
    except ScriptIngestionError as exc:
        logger.warning("Mermaid rendering skipped: script ingestion error: %s", exc)
        return None
    except Exception:
        logger.warning("Mermaid rendering failed", exc_info=True)
        return None


def render_mermaid_from_graph_payload_full_env(
    graph_payload: dict[str, Any],
) -> str | None:
    """Render Mermaid from a stored graph payload using the full Python environment.

    Mirrors :func:`render_mermaid_from_graph_payload` but skips the script
    size limit.  Only call this from trusted server-side contexts (e.g.,
    ingest-time pre-computation) where the script has already been size-validated.
    """
    fmt = graph_payload.get("format", "")
    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        return None
    source = graph_payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    entrypoint = graph_payload.get("entrypoint")
    return _render_mermaid_from_script_full_env(source, entrypoint)


__all__ = [
    "render_mermaid_from_graph_payload",
    "render_mermaid_from_graph_payload_full_env",
]
