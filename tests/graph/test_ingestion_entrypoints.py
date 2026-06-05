"""Tests for ingesting LangGraph scripts and resolving entrypoints."""

from __future__ import annotations
import textwrap
import pytest
from orcheo.graph.builder import build_graph
from orcheo.graph.ingestion import (
    LANGGRAPH_SCRIPT_FORMAT,
    ScriptIngestionError,
    ingest_langgraph_script,
)


def _assert_payload_index(payload: dict[str, object], *, node_name: str) -> None:
    index = payload.get("index")
    summary = payload.get("summary")
    assert isinstance(index, dict)
    assert isinstance(summary, dict)
    summary_nodes = summary.get("nodes")
    assert isinstance(summary_nodes, list)
    assert any(
        isinstance(node, dict) and node.get("name") == node_name
        for node in summary_nodes
    )
    cron = index.get("cron")
    assert isinstance(cron, list)
    mermaid = index.get("mermaid")
    assert isinstance(mermaid, str)
    assert node_name in mermaid


def test_ingest_script_with_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State
        from orcheo.nodes.connectors.rss import RSSNode

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("rss", RSSNode(name="rss", sources=["https://example.com/feed"]))
            graph.set_entry_point("rss")
            graph.set_finish_point("rss")
            return graph
        """
    )

    payload = ingest_langgraph_script(script, entrypoint="build_graph")

    assert payload["format"] == LANGGRAPH_SCRIPT_FORMAT
    assert payload["entrypoint"] == "build_graph"
    _assert_payload_index(payload, node_name="rss")

    graph = build_graph(payload)
    assert set(graph.nodes.keys()) == {"rss"}


def test_ingest_script_without_entrypoint_auto_discovers_graph() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.add_node("first", lambda state: state)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        """
    )

    payload = ingest_langgraph_script(script)

    assert payload["entrypoint"] is None
    _assert_payload_index(payload, node_name="first")


def test_ingest_script_with_async_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        async def build_graph():
            graph = StateGraph(State)
            graph.add_node("first", lambda state: state)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph
        """
    )

    payload = ingest_langgraph_script(script, entrypoint="build_graph")

    assert payload["entrypoint"] == "build_graph"
    _assert_payload_index(payload, node_name="first")


def test_ingest_script_awaits_top_level_coroutine() -> None:
    script = textwrap.dedent(
        """
        import asyncio

        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        async def build_graph():
            await asyncio.sleep(0)
            graph = StateGraph(State)
            graph.add_node("first", lambda state: state)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph

        orcheo_workflow = await build_graph()
        """
    )

    payload = ingest_langgraph_script(script)

    assert payload["entrypoint"] is None
    _assert_payload_index(payload, node_name="first")


def test_ingest_script_with_multiple_candidates_requires_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        first = StateGraph(State)
        second = StateGraph(State)
        """
    )

    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script(script)


def test_ingest_script_defaults_to_orcheo_workflow_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("first", lambda state: state)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph

        def extra_graph() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("second", lambda state: state)
            graph.set_entry_point("second")
            graph.set_finish_point("second")
            return graph

        orcheo_workflow = build_graph
        """
    )

    payload = ingest_langgraph_script(script)

    assert payload["entrypoint"] is None
    _assert_payload_index(payload, node_name="first")


def test_ingest_script_allows_previously_blocked_imports() -> None:
    script = textwrap.dedent(
        """
        import os
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        """
    )

    result = ingest_langgraph_script(script)
    assert result["format"] == LANGGRAPH_SCRIPT_FORMAT


def test_ingest_script_rejects_relative_imports() -> None:
    script = "from .foo import bar"

    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script(script)


def test_ingest_script_allows_csv_import() -> None:
    script = textwrap.dedent(
        f"""
        import csv
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.add_node("first", lambda state: state)
        graph.set_entry_point("first")
        graph.set_finish_point("first")

        rows = list(csv.reader(["a,b"]))
        """
    )

    payload = ingest_langgraph_script(script)

    assert payload["entrypoint"] is None
    _assert_payload_index(payload, node_name="first")


def test_ingest_script_missing_entrypoint_errors() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        """
    )

    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script(script, entrypoint="missing")


def test_ingest_script_without_candidates_errors() -> None:
    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script("value = 42")


def test_ingest_script_entrypoint_requires_arguments() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph(name: str):
            graph = StateGraph(State)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph
        """
    )

    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script(script, entrypoint="build_graph")


def test_ingest_script_handles_compiled_graph_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.add_node("first", lambda state: state)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        compiled = graph.compile()
        """
    )

    payload = ingest_langgraph_script(script, entrypoint="compiled")

    _assert_payload_index(payload, node_name="first")


def test_ingest_script_entrypoint_not_resolvable() -> None:
    script = textwrap.dedent(
        """
        class Dummy:
            pass

        candidate = Dummy()
        """
    )

    with pytest.raises(ScriptIngestionError):
        ingest_langgraph_script(script, entrypoint="candidate")


def test_ingest_script_ignores_non_graph_functions() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        async def run_demo() -> None:
            raise RuntimeError("should not execute during ingestion")

        def build_graph() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("first", lambda state: state)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph
        """
    )

    payload = ingest_langgraph_script(script)

    assert payload["entrypoint"] is None
    _assert_payload_index(payload, node_name="first")
