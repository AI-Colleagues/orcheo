"""Graph builder module for Orcheo."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langgraph.graph import StateGraph
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT, load_graph_from_script


class UnsupportedWorkflowGraphFormatError(ValueError):
    """Raised when runtime receives a non-script workflow graph payload."""


def _describe_graph_format(graph_json: Mapping[str, Any]) -> str:
    """Return a human-readable format label for error messages."""
    graph_format = graph_json.get("format")
    if isinstance(graph_format, str) and graph_format.strip():
        return graph_format
    return "unknown"


def build_graph(graph_json: Mapping[str, Any]) -> StateGraph:
    """Build a LangGraph graph from a configuration payload."""
    if graph_json.get("format") != LANGGRAPH_SCRIPT_FORMAT:
        observed_format = _describe_graph_format(graph_json)
        msg = (
            "Unsupported workflow graph format "
            f"'{observed_format}'. Only '{LANGGRAPH_SCRIPT_FORMAT}' workflow "
            "versions can execute. Re-ingest this workflow from a Python script."
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
