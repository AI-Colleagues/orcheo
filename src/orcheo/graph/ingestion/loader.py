"""Load LangGraph StateGraph instances from Python scripts."""

from __future__ import annotations
import ast
import asyncio
import builtins
import inspect
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, get_origin
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from orcheo.graph.ingestion.config import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.sandbox import (
    compile_langgraph_script,
    create_sandbox_namespace,
    execution_timeout,
    is_sandbox_enabled,
    validate_script_size,
)


def _execute_langgraph_script(
    source: str,
    max_script_bytes: int | None,
    execution_timeout_seconds: float | None,
) -> dict[str, Any]:
    """Execute the LangGraph script inside the RP sandbox and return its namespace."""
    validate_script_size(source, max_script_bytes)
    if is_sandbox_enabled():
        namespace = create_sandbox_namespace()
        compiled = compile_langgraph_script(source)
    else:
        namespace = {"__builtins__": vars(builtins), "__name__": "__orcheo_ingest__"}
        compiled = compile(  # noqa: S307
            source, "<langgraph-script>", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        )
    try:
        with execution_timeout(execution_timeout_seconds):
            result = eval(compiled, namespace)  # noqa: S307
            if inspect.isawaitable(result):
                _run_awaitable(result)
    except ScriptIngestionError:
        raise
    except SyntaxError as exc:
        message = f"Compilation error: {exc}"
        raise ScriptIngestionError(message) from exc
    except TimeoutError as exc:
        message = "LangGraph script execution exceeded the configured timeout"
        raise ScriptIngestionError(message) from exc
    except Exception as exc:  # pragma: no cover - exercised via tests
        message = f"Runtime error during script execution: {type(exc).__name__}: {exc}"
        raise ScriptIngestionError(message) from exc

    return namespace


def _resolve_default_workflow(namespace: dict[str, Any]) -> StateGraph | None:
    """Return the default workflow entrypoint graph when the symbol is defined."""
    workflow_candidate = namespace.get("orcheo_workflow")
    if workflow_candidate is None:
        return None
    return _resolve_graph(workflow_candidate)


def _collect_graph_candidates(
    namespace: dict[str, Any],
    module_name: str,
    entrypoint: str | None,
) -> list[Any]:
    """Collect potential graph entrypoints from the execution namespace."""
    if entrypoint is not None:
        if entrypoint not in namespace:
            msg = f"Entrypoint '{entrypoint}' not found in script"
            raise ScriptIngestionError(msg)
        return [namespace[entrypoint]]

    candidates = [
        value for value in namespace.values() if _is_graph_candidate(value, module_name)
    ]
    if not candidates:
        msg = "Script did not produce a LangGraph StateGraph"
        raise ScriptIngestionError(msg)
    return candidates


def load_graph_from_script(
    source: str,
    *,
    entrypoint: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
    execution_timeout_seconds: float | None = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> StateGraph:
    """Execute a LangGraph Python script inside the RP sandbox and return the graph."""
    namespace = _execute_langgraph_script(
        source, max_script_bytes, execution_timeout_seconds
    )

    module_name = namespace["__name__"]

    if entrypoint is None:
        resolved = _resolve_default_workflow(namespace)
        if resolved is not None:
            return resolved

    candidates = _collect_graph_candidates(namespace, module_name, entrypoint)

    resolved_graphs = [
        graph for candidate in candidates if (graph := _resolve_graph(candidate))
    ]

    if not resolved_graphs:
        msg = "Unable to resolve a LangGraph StateGraph from the script"
        raise ScriptIngestionError(msg)

    if entrypoint is None and len(resolved_graphs) > 1:
        msg = "Multiple StateGraph candidates discovered; specify an entrypoint"
        raise ScriptIngestionError(msg)

    return resolved_graphs[0]


def _is_graph_candidate(obj: Any, module_name: str) -> bool:
    """Return ``True`` when ``obj`` may resolve to a ``StateGraph``."""
    if isinstance(obj, StateGraph | CompiledStateGraph):
        return True

    if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
        if getattr(obj, "__module__", "") != module_name:
            return False
        return _returns_state_graph(obj)

    return False


async def _await_awaitable(awaitable: Awaitable[Any]) -> Any:
    """Await ``awaitable`` within a coroutine context."""
    return await awaitable


def _run_awaitable(awaitable: Awaitable[Any]) -> Any:
    """Execute ``awaitable`` from synchronous ingestion code."""
    if _is_event_loop_running():
        return _run_awaitable_in_thread(awaitable)

    try:
        awaitable_wrapper = _await_awaitable(awaitable)
        return asyncio.run(awaitable_wrapper)
    except RuntimeError:
        awaitable_wrapper.close()
        if inspect.iscoroutine(awaitable) and (
            inspect.getcoroutinestate(awaitable) is not inspect.CORO_CREATED
        ):
            raise
        return _run_awaitable_with_new_loop(awaitable)


def _resolve_graph(obj: Any) -> StateGraph | None:
    """Return a ``StateGraph`` from the supplied object if possible."""
    resolved: StateGraph | None = None

    if isinstance(obj, StateGraph):
        resolved = obj
    elif isinstance(obj, CompiledStateGraph):
        resolved = obj.builder
    elif inspect.isawaitable(obj):
        result = _run_awaitable(obj)
        resolved = _resolve_graph(result)
    elif callable(obj):
        signature = inspect.signature(obj)
        if any(
            parameter.default is inspect.Parameter.empty
            and parameter.kind
            not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
            for parameter in signature.parameters.values()
        ):
            return None
        try:
            result = obj()
        except Exception:  # pragma: no cover - the caller will raise a clearer error
            return None
        resolved = _resolve_graph(result)

    return resolved


def _is_event_loop_running() -> bool:
    """Return ``True`` when called from an active asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_awaitable_in_thread(awaitable: Awaitable[Any]) -> Any:
    """Execute ``awaitable`` on a dedicated thread to avoid loop nesting."""

    def runner() -> Any:
        return asyncio.run(_await_awaitable(awaitable))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner)
        return future.result()


def _run_awaitable_with_new_loop(awaitable: Awaitable[Any]) -> Any:
    """Execute ``awaitable`` by creating a temporary event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_await_awaitable(awaitable))
    finally:
        loop.close()


def _returns_state_graph(callable_obj: Any) -> bool:
    """Return ``True`` when ``callable_obj`` is annotated to return a graph."""
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    annotation = signature.return_annotation
    if annotation is inspect.Signature.empty:
        return False
    return _is_state_graph_annotation(annotation)


def _is_state_graph_annotation(annotation: Any) -> bool:
    """Return ``True`` when ``annotation`` refers to a StateGraph type."""
    if isinstance(annotation, str):
        return annotation in {"StateGraph", "CompiledStateGraph"}
    origin = get_origin(annotation)
    if origin is not None:
        return origin in (StateGraph, CompiledStateGraph)
    return annotation in (StateGraph, CompiledStateGraph)


__all__ = ["load_graph_from_script"]
