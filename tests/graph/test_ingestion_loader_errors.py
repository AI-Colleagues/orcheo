"""Tests for ingestion loader error handling."""

from __future__ import annotations
import asyncio
import inspect
import pytest
from orcheo.graph.ingestion import loader
from orcheo.graph.ingestion.exceptions import ScriptIngestionError


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


def test_execute_langgraph_script_reraises_script_ingestion_error() -> None:
    """ScriptIngestionError raised during script execution is re-raised (line 168)."""
    source = (
        "from orcheo.graph.ingestion.exceptions import ScriptIngestionError\n"
        "raise ScriptIngestionError('propagated from script')\n"
    )
    with pytest.raises(ScriptIngestionError, match="propagated from script"):
        loader._execute_langgraph_script(source, None, None)


def test_is_graph_candidate_returns_false_for_wrong_module() -> None:
    """Function from a different module is rejected as a candidate (line 254)."""
    from langgraph.graph import StateGraph

    def builder() -> StateGraph: ...  # type: ignore[empty-body]

    builder.__module__ = "some.other.module"
    assert loader._is_graph_candidate(builder, "__orcheo_ingest__") is False


def test_run_awaitable_reraises_runtime_error_from_coroutine() -> None:
    """RuntimeError raised inside a coroutine is re-raised by _run_awaitable (line 278)."""

    async def _raising() -> None:
        raise RuntimeError("boom from coroutine")

    coro = _raising()
    with pytest.raises(RuntimeError, match="boom from coroutine"):
        loader._run_awaitable(coro)
