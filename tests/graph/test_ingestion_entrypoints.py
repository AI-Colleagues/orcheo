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
from orcheo.graph.ingestion.loader import load_graph_from_script


def test_ingest_script_returns_format_and_source() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("rss_node", lambda state: state)
            graph.set_entry_point("rss_node")
            graph.set_finish_point("rss_node")
            return graph
        """
    )

    payload = ingest_langgraph_script(script, entrypoint="build_graph")

    assert payload["format"] == LANGGRAPH_SCRIPT_FORMAT
    assert payload["entrypoint"] == "build_graph"
    assert payload["source"] == script
    index = payload["index"]
    assert isinstance(index, dict)
    assert "cron" in index
    assert "listeners" in index


def test_ingest_script_no_execution_during_ingestion() -> None:
    """ingest_langgraph_script should not execute the script — only compile it."""
    script = textwrap.dedent(
        """
        raise RuntimeError("this should not execute during ingestion")
        """
    )

    # Should not raise — ingestion only compiles, doesn't execute
    payload = ingest_langgraph_script(script)
    assert payload["format"] == LANGGRAPH_SCRIPT_FORMAT


def test_ingest_script_rejects_syntax_errors() -> None:
    with pytest.raises(ScriptIngestionError, match="[Cc]ompilation"):
        ingest_langgraph_script("def broken(:\n    pass")


def test_load_graph_from_script_with_entrypoint() -> None:
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

    graph = load_graph_from_script(script, entrypoint="build_graph")
    assert set(graph.nodes.keys()) == {"rss"}


def test_load_graph_from_script_allows_graph_state_import() -> None:
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

    graph = load_graph_from_script(script)

    assert "first" in graph.nodes


def test_load_graph_from_script_auto_discovers_graph() -> None:
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

    graph = load_graph_from_script(script)
    assert "first" in graph.nodes


def test_load_graph_from_script_with_async_entrypoint() -> None:
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

    graph = load_graph_from_script(script, entrypoint="build_graph")
    assert "first" in graph.nodes


def test_load_graph_async_entrypoint_with_script_defined_typeddict_state() -> None:
    # Regression: an async entrypoint builds its StateGraph after the script
    # module has returned. If the loader unregisters the script module from
    # sys.modules before the graph is built, get_type_hints() can no longer
    # resolve the ForwardRefs in a script-defined TypedDict state subclass and
    # fails with "NameError: name 'Any' is not defined".
    script = textwrap.dedent(
        """
        from typing import Any

        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        class CustomState(State, total=False):
            payload: dict[str, Any] | None

        async def orcheo_workflow():
            graph = StateGraph(CustomState)
            graph.add_node("first", lambda state: state)
            graph.set_entry_point("first")
            graph.set_finish_point("first")
            return graph
        """
    )

    graph = load_graph_from_script(script, entrypoint="orcheo_workflow")
    assert "first" in graph.nodes


def test_load_graph_awaits_top_level_coroutine() -> None:
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

    graph = load_graph_from_script(script)
    assert "first" in graph.nodes


def test_load_graph_with_multiple_candidates_requires_entrypoint() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        first = StateGraph(State)
        second = StateGraph(State)
        """
    )

    with pytest.raises(ScriptIngestionError):
        load_graph_from_script(script)


def test_load_graph_defaults_to_orcheo_workflow_entrypoint() -> None:
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

    graph = load_graph_from_script(script)
    assert "first" in graph.nodes


def test_ingest_script_allows_unrestricted_imports() -> None:
    """Ingestion does not block imports — only syntax is checked at ingest time."""
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


def test_load_graph_rejects_relative_imports() -> None:
    """Relative imports are rejected during script execution."""
    script = "from .foo import bar"

    with pytest.raises(ScriptIngestionError):
        load_graph_from_script(script)


def test_load_graph_from_script_missing_entrypoint_errors() -> None:
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
        load_graph_from_script(script, entrypoint="missing")


def test_load_graph_without_candidates_errors() -> None:
    with pytest.raises(ScriptIngestionError):
        load_graph_from_script("value = 42")


def test_load_graph_entrypoint_requires_no_arguments() -> None:
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
        load_graph_from_script(script, entrypoint="build_graph")


def test_load_graph_handles_compiled_graph_entrypoint() -> None:
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

    result = load_graph_from_script(script, entrypoint="compiled")
    assert "first" in result.nodes


def test_load_graph_ignores_non_graph_functions() -> None:
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

    graph = load_graph_from_script(script)
    assert "first" in graph.nodes


def test_build_graph_round_trips_through_ingest() -> None:
    """build_graph can consume a payload produced by ingest_langgraph_script."""
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
    graph = build_graph(payload)
    assert set(graph.nodes.keys()) == {"rss"}


def test_ingest_script_reraises_script_ingestion_error() -> None:
    """ingest_langgraph_script wraps SyntaxError as ScriptIngestionError."""
    with pytest.raises(ScriptIngestionError, match="[Cc]ompilation"):
        ingest_langgraph_script("def broken(:\n    pass")
