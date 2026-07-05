"""Tests for BaseNode fallback resolution logic."""

from __future__ import annotations
from typing import cast
from orcheo.graph.state import State
from orcheo.nodes.base import BaseNode


def test_fallback_to_node_results_requires_mapping() -> None:
    state = cast(State, {"node_results": ["unexpected"]})

    fallback = BaseNode._fallback_to_node_results(["output", "value"], 0, state)

    assert fallback is None
