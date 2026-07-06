"""Shared runtime state assembly helpers."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.runtime.attachments import hydrate_attachment_runtime_config


def build_initial_state(
    graph_config: Mapping[str, Any],
    inputs: Any,
    runtime_config: Mapping[str, Any] | None = None,
    workspace_id: str | None = None,
) -> Any:
    """Return the initial workflow state used by runtime entrypoints."""
    runtime_state_config = hydrate_attachment_runtime_config(runtime_config)

    if graph_config.get("format") == LANGGRAPH_SCRIPT_FORMAT:
        if not isinstance(inputs, Mapping):
            return inputs
        state = dict(inputs)
        state.setdefault("inputs", dict(inputs))
        state.setdefault("node_results", {})
        state.setdefault("messages", [])
        state["workspace_id"] = workspace_id
        state["config"] = runtime_state_config
        return state

    normalized_inputs = dict(inputs) if isinstance(inputs, Mapping) else inputs
    return {
        "workspace_id": workspace_id,
        "messages": [],
        "node_results": {},
        "inputs": normalized_inputs,
        "config": runtime_state_config,
    }


__all__ = ["build_initial_state"]
