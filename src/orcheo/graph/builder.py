"""Graph builder module for Orcheo."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langgraph.graph import StateGraph
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT, load_graph_from_script


class UnsupportedWorkflowGraphFormatError(ValueError):
    """Raised when runtime receives an unsupported workflow graph payload."""


def build_graph(graph_json: Mapping[str, Any]) -> StateGraph:
    """Build a LangGraph graph from a langgraph-script configuration payload."""
    fmt = graph_json.get("format")

    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        observed = fmt if isinstance(fmt, str) and fmt.strip() else "unknown"
        msg = (
            f"Unsupported workflow graph format '{observed}'. "
            f"Only '{LANGGRAPH_SCRIPT_FORMAT}' workflow versions can execute."
        )
        raise UnsupportedWorkflowGraphFormatError(msg)

    source = graph_json.get("source")
    if not isinstance(source, str) or not source.strip():
        msg = "Script graph configuration requires a non-empty source"
        raise ValueError(msg)
    entrypoint_value = graph_json.get("entrypoint")
    if entrypoint_value is not None and not isinstance(entrypoint_value, str):
        msg = "Entrypoint must be a string when provided"
        raise ValueError(msg)
    return load_graph_from_script(
        source, entrypoint=entrypoint_value, max_script_bytes=None
    )
