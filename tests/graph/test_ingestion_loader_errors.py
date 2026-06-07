"""Tests for ingestion loader error handling."""

from __future__ import annotations
import builtins
import pytest
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
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
    """ScriptIngestionError raised during script execution is re-raised."""
    source = (
        "from orcheo.graph.ingestion.exceptions import ScriptIngestionError\n"
        "raise ScriptIngestionError('propagated from script')\n"
    )
    with pytest.raises(ScriptIngestionError, match="propagated from script"):
        loader._execute_langgraph_script(source, None, None)


def test_is_graph_candidate_returns_false_for_wrong_module() -> None:
    """Function from a different module is rejected as a candidate."""
    from langgraph.graph import StateGraph

    def builder() -> StateGraph: ...  # type: ignore[empty-body]

    builder.__module__ = "some.other.module"
    assert loader._is_graph_candidate(builder, "__orcheo_ingest__") is False


def test_run_awaitable_reraises_runtime_error_from_coroutine() -> None:
    """RuntimeError raised inside a coroutine is re-raised by _run_awaitable."""

    async def _raising() -> None:
        raise RuntimeError("boom from coroutine")

    coro = _raising()
    with pytest.raises(RuntimeError, match="boom from coroutine"):
        loader._run_awaitable(coro)


# ---------------------------------------------------------------------------
# Top-level awaitable returned from eval
# ---------------------------------------------------------------------------


def test_execute_langgraph_script_top_level_await() -> None:
    """When eval() returns an awaitable, _run_awaitable is called."""
    source = "import asyncio\nresult = await asyncio.sleep(0)\n"
    namespace = loader._execute_langgraph_script(source, None, None)
    assert "result" in namespace


# ---------------------------------------------------------------------------
# SyntaxError from eval
# ---------------------------------------------------------------------------


def test_execute_langgraph_script_syntax_error_from_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SyntaxError raised inside eval is wrapped as ScriptIngestionError."""
    original_eval = builtins.eval

    def _raise_syntax(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SyntaxError("injected syntax error")

    monkeypatch.setattr(builtins, "eval", _raise_syntax)
    try:
        with pytest.raises(ScriptIngestionError, match="Compilation error"):
            loader._execute_langgraph_script("x = 1", None, None)
    finally:
        builtins.eval = original_eval


# ---------------------------------------------------------------------------
# _resolve_default_workflow returns graph and load_graph_from_script returns it
# ---------------------------------------------------------------------------


def test_load_graph_uses_orcheo_workflow_entrypoint() -> None:
    """When orcheo_workflow is set, load_graph_from_script returns it directly."""
    from orcheo.graph.state import State  # noqa: F401

    source = """
from langgraph.graph import StateGraph
from orcheo.graph.state import State

def build_graph():
    graph = StateGraph(State)
    graph.add_node("step", lambda state: state)
    graph.set_entry_point("step")
    graph.set_finish_point("step")
    return graph

orcheo_workflow = build_graph
"""
    graph = loader.load_graph_from_script(source)
    assert "step" in graph.nodes


# ---------------------------------------------------------------------------
# _collect_graph_candidates entrypoint not found
# ---------------------------------------------------------------------------


def test_collect_graph_candidates_missing_entrypoint() -> None:
    """Requesting a nonexistent entrypoint raises ScriptIngestionError."""
    with pytest.raises(ScriptIngestionError, match="not found"):
        loader._collect_graph_candidates({"x": 1}, "__main__", "missing")


# ---------------------------------------------------------------------------
# load_graph_from_script: no resolved graphs
# ---------------------------------------------------------------------------


def test_load_graph_from_script_no_resolvable_graphs() -> None:
    """When candidates exist but none resolve to a StateGraph, raise."""
    source = """
from langgraph.graph import StateGraph

def build() -> StateGraph:
    return 42  # type: ignore
"""
    with pytest.raises(ScriptIngestionError, match="Unable to resolve"):
        loader.load_graph_from_script(source)


# ---------------------------------------------------------------------------
# load_graph_from_script: multiple graphs without entrypoint
# ---------------------------------------------------------------------------


def test_load_graph_from_script_multiple_candidates() -> None:
    """Multiple StateGraph candidates without entrypoint raises."""
    source = """
from langgraph.graph import StateGraph

first = StateGraph(dict)
second = StateGraph(dict)
"""
    with pytest.raises(ScriptIngestionError, match="Multiple StateGraph"):
        loader.load_graph_from_script(source)


# ---------------------------------------------------------------------------
# _is_graph_candidate returns True when module and annotation match
# ---------------------------------------------------------------------------


def test_is_graph_candidate_right_module_returns_state_graph() -> None:
    """Function with StateGraph return annotation in correct module is a candidate."""

    def builder() -> StateGraph: ...  # type: ignore[empty-body]

    builder.__module__ = "__orcheo_ingest__"
    assert loader._is_graph_candidate(builder, "__orcheo_ingest__") is True


# ---------------------------------------------------------------------------
# _resolve_graph: CompiledStateGraph → obj.builder
# ---------------------------------------------------------------------------


def test_resolve_graph_compiled_state_graph() -> None:
    """_resolve_graph extracts .builder from a CompiledStateGraph."""
    graph = StateGraph(dict)
    graph.add_node("n", lambda s: s)
    graph.set_entry_point("n")
    graph.set_finish_point("n")
    compiled = graph.compile()
    result = loader._resolve_graph(compiled)
    assert result is graph


# ---------------------------------------------------------------------------
# _resolve_graph: callable with required params → None
# ---------------------------------------------------------------------------


def test_resolve_graph_callable_with_required_params() -> None:
    """Callable requiring mandatory arguments returns None."""

    def factory(required_arg: str) -> StateGraph:  # type: ignore[empty-body]
        ...

    result = loader._resolve_graph(factory)
    assert result is None


# ---------------------------------------------------------------------------
# _resolve_graph: awaitable
# ---------------------------------------------------------------------------


def test_resolve_graph_awaitable() -> None:
    """_resolve_graph resolves an awaitable that returns a StateGraph."""
    graph = StateGraph(dict)

    async def _builder() -> StateGraph:
        return graph

    result = loader._resolve_graph(_builder())
    assert result is graph


# ---------------------------------------------------------------------------
# _run_awaitable_with_new_loop
# ---------------------------------------------------------------------------


def test_run_awaitable_with_new_loop() -> None:
    """_run_awaitable_with_new_loop runs a coroutine on a fresh event loop."""

    async def _coro() -> int:
        return 99

    result = loader._run_awaitable_with_new_loop(_coro())
    assert result == 99


# ---------------------------------------------------------------------------
# _returns_state_graph: non-empty annotation path
# ---------------------------------------------------------------------------


def test_returns_state_graph_with_state_graph_annotation() -> None:
    """_returns_state_graph returns True for StateGraph-annotated callables."""

    def builder() -> StateGraph: ...  # type: ignore[empty-body]

    assert loader._returns_state_graph(builder) is True


# ---------------------------------------------------------------------------
# _is_state_graph_annotation: plain class path
# ---------------------------------------------------------------------------


def test_is_state_graph_annotation_direct_class() -> None:
    """Direct StateGraph or CompiledStateGraph annotation matches."""
    assert loader._is_state_graph_annotation(StateGraph) is True
    assert loader._is_state_graph_annotation(CompiledStateGraph) is True
    assert loader._is_state_graph_annotation(int) is False


# ---------------------------------------------------------------------------
# load_graph_from_script_full_env
# ---------------------------------------------------------------------------


def test_load_graph_from_script_full_env_resolves_graph() -> None:
    """load_graph_from_script_full_env resolves a simple StateGraph."""
    source = """
from langgraph.graph import StateGraph
from orcheo.graph.state import State

def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("step", lambda s: s)
    g.set_entry_point("step")
    g.set_finish_point("step")
    return g
"""
    graph = loader.load_graph_from_script_full_env(source, entrypoint="build")
    assert "step" in graph.nodes


def test_load_graph_from_script_full_env_allows_any_import() -> None:
    """load_graph_from_script_full_env succeeds with unrestricted imports."""
    source = """
import orcheo.runtime.attachments
from langgraph.graph import StateGraph
from orcheo.graph.state import State

def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("step", lambda s: s)
    g.set_entry_point("step")
    g.set_finish_point("step")
    return g
"""
    graph = loader.load_graph_from_script_full_env(source, entrypoint="build")
    assert "step" in graph.nodes


def test_load_graph_from_script_full_env_raises_on_syntax_error() -> None:
    """Syntax errors are wrapped as ScriptIngestionError."""
    with pytest.raises(ScriptIngestionError):
        loader.load_graph_from_script_full_env("def bad syntax!!!")
