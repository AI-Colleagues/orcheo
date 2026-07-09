"""Graph builder module for Orcheo."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langgraph.graph import StateGraph
from orcheo.graph.ingestion import (
    FROZEN_IR_FORMAT,
    LANGGRAPH_SCRIPT_FORMAT,
    load_graph_from_script,
)


class UnsupportedWorkflowGraphFormatError(ValueError):
    """Raised when runtime receives an unsupported workflow graph payload."""


def build_graph(graph_json: Mapping[str, Any]) -> StateGraph:
    """Build a LangGraph graph from a stored workflow graph payload.

    Restricted-mode versions store a ``frozen-ir`` payload and are rebuilt from
    the IR via the trusted rebuilder (with ``CodeNode`` bodies sandboxed);
    unrestricted-mode versions store a ``langgraph-script`` payload and use the
    in-process script loader. Execution branches on the *stored* format, so a
    version always runs the way it was ingested.
    """
    fmt = graph_json.get("format")

    if fmt == FROZEN_IR_FORMAT:
        return _build_from_frozen_ir(graph_json)

    if fmt != LANGGRAPH_SCRIPT_FORMAT:
        observed = fmt if isinstance(fmt, str) and fmt.strip() else "unknown"
        msg = (
            f"Unsupported workflow graph format '{observed}'. "
            f"Only '{LANGGRAPH_SCRIPT_FORMAT}' and '{FROZEN_IR_FORMAT}' workflow "
            "versions can execute."
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
        source,
        entrypoint=entrypoint_value,
        max_script_bytes=None,
        script_filename=(
            graph_json.get("filename")
            if isinstance(graph_json.get("filename"), str)
            else None
        ),
    )


def _build_from_frozen_ir(graph_json: Mapping[str, Any]) -> StateGraph:
    """Rebuild a graph from a stored frozen IR with CodeNodes sandboxed."""
    from orcheo.graph.ir.definition_mode import log_active_definition_mode
    from orcheo.sandbox.code_node import build_sandboxed_state_graph

    log_active_definition_mode()
    ir = graph_json.get("ir")
    if not isinstance(ir, Mapping):
        msg = "Frozen-IR graph configuration requires an 'ir' mapping"
        raise ValueError(msg)
    return build_sandboxed_state_graph(ir)
