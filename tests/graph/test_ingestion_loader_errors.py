"""Tests for ingestion loader error handling."""

from __future__ import annotations
from orcheo.graph.ingestion import loader


def test_returns_state_graph_handles_type_error() -> None:
    assert loader._returns_state_graph(object()) is False


def test_returns_state_graph_handles_value_error() -> None:
    assert loader._returns_state_graph(type) is False


def test_returns_state_graph_requires_return_annotation() -> None:
    def builder():
        return None

    assert loader._returns_state_graph(builder) is False


def test_is_state_graph_annotation_accepts_forward_refs() -> None:
    assert loader._is_state_graph_annotation("StateGraph") is True
    assert loader._is_state_graph_annotation("CompiledStateGraph") is True


def test_is_state_graph_annotation_accepts_generic_origin(monkeypatch) -> None:
    sentinel = object()
    original_get_origin = loader.get_origin

    def fake_get_origin(value):
        if value is sentinel:
            return loader.StateGraph
        return original_get_origin(value)

    monkeypatch.setattr(loader, "get_origin", fake_get_origin)
    assert loader._is_state_graph_annotation(sentinel) is True
