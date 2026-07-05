"""Shared helpers for manipulating workflow state inside node implementations."""

from __future__ import annotations
import copy
from collections.abc import Mapping, Sequence
from typing import Any
from orcheo.graph.state import State


def normalise_state_snapshot(state: State) -> dict[str, Any]:
    """Return a serialisable snapshot of ``state`` for debugging."""
    inputs: dict[str, Any] = {}
    if isinstance(state.get("inputs"), Mapping):
        inputs = dict(state["inputs"])

    node_results: dict[str, Any] = {}
    if isinstance(state.get("node_results"), Mapping):
        node_results = copy.deepcopy(state["node_results"])

    return {"inputs": inputs, "node_results": node_results}


def extract_from_state(state: State, path: str) -> tuple[bool, Any]:
    """Return whether ``path`` exists in ``state`` and its associated value."""
    if not path:
        msg = "tap_path must be a non-empty string"
        raise ValueError(msg)

    current: Any = state.get("node_results", {})
    segments = [segment.strip() for segment in path.split(".") if segment.strip()]
    if not segments:
        msg = "tap_path must contain at least one segment"
        raise ValueError(msg)

    for segment in segments:
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, Sequence) and not isinstance(current, str | bytes):
            try:
                index = int(segment)
            except ValueError:
                return False, None
            if index < 0 or index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


__all__ = ["normalise_state_snapshot", "extract_from_state"]
