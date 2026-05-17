"""Tests for ingestion helper utilities that serialise graph metadata."""

from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from langgraph.graph import StateGraph
from orcheo.graph.ingestion import _resolve_graph, _serialise_branch, _unwrap_runnable
from orcheo.nodes.connectors.rss import RSSNode


def test_unwrap_runnable_prefers_wrapped_func() -> None:
    node = RSSNode(name="rss", sources=["https://example.com/feed"])
    wrapper = SimpleNamespace(func=node)

    assert _unwrap_runnable(wrapper) is node


def test_serialise_branch_with_mapping_and_default() -> None:
    branch = SimpleNamespace(
        ends={"success": "__start__", "failure": "__end__"},
        then="__end__",
        path=SimpleNamespace(func=lambda: None),
    )

    payload = _serialise_branch("node", "result", branch)

    assert payload["mapping"] == {"success": "START", "failure": "END"}
    assert payload["default"] == "END"
    assert payload["callable"] == "<lambda>"


def test_serialise_branch_without_optional_fields() -> None:
    branch = SimpleNamespace(ends=None, then=None)

    payload = _serialise_branch("node", "result", branch)

    assert payload == {"source": "node", "branch": "result"}


def test_resolve_graph_with_unknown_object_returns_none() -> None:
    assert _resolve_graph(object()) is None


def test_resolve_graph_handles_asyncio_run_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def build_graph() -> StateGraph:
        return StateGraph(dict)

    def fake_asyncio_run(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    graph = _resolve_graph(build_graph())

    assert isinstance(graph, StateGraph)


@pytest.mark.asyncio
async def test_resolve_graph_handles_running_event_loop() -> None:
    async def build_graph() -> StateGraph:
        return StateGraph(dict)

    graph = _resolve_graph(build_graph())

    assert isinstance(graph, StateGraph)
