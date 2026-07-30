"""Load LangGraph StateGraph instances from Python scripts."""

from __future__ import annotations
import ast
import asyncio
import builtins
import inspect
import sys
import threading
import types
import uuid
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, get_origin
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from orcheo.graph.ingestion.config import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.sandbox import (
    execution_timeout,
    remaining_execution_time,
    validate_script_size,
)


_SCRIPT_MODULE_NAME_PREFIX = "__orcheo_ingest__"


def _new_script_module_name() -> str:
    """Return a unique temporary module name for one script ingestion."""
    return f"{_SCRIPT_MODULE_NAME_PREFIX}_{uuid.uuid4().hex}"


def _execute_langgraph_script(
    source: str,
    max_script_bytes: int | None,
    execution_timeout_seconds: float | None,
    script_filename: str | None = None,
) -> dict[str, Any]:
    """Execute a LangGraph script with full Python builtins and return its namespace.

    The script is executed inside a temporary module registered in ``sys.modules``
    so that decorators like ``@dataclass`` can resolve ``sys.modules[cls.__module__]``
    without raising ``AttributeError``.

    On success the module is intentionally **left registered** in ``sys.modules``:
    ``StateGraph`` resolves its ``TypedDict`` state schema with
    ``typing.get_type_hints``, which evaluates the annotations' ``ForwardRef``s
    against ``sys.modules[<schema module>].__dict__``.  For an async
    ``orcheo_workflow`` entrypoint the graph is only built once the coroutine is
    awaited in :func:`_load_graph_from_namespace`, i.e. after this function
    returns, so the module must still be present then or ``Any`` (and every other
    imported name) resolves to ``NameError``.  The caller is responsible for
    removing it once graph resolution completes.
    """
    validate_script_size(source, max_script_bytes)

    # Register a real module so that @dataclass and similar decorators that look
    # up sys.modules[cls.__module__] do not encounter None.
    module_name = _new_script_module_name()
    module = types.ModuleType(module_name)
    filename = script_filename or "<langgraph-script>"
    module.__file__ = filename
    module.__package__ = ""
    module.__dict__["__builtins__"] = vars(builtins)
    namespace = module.__dict__
    sys.modules[module_name] = module

    try:
        try:
            compiled = compile(  # noqa: S307
                source, filename, "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
            )
        except SyntaxError as exc:
            raise ScriptIngestionError(f"Compilation error: {exc}") from exc
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
            message = (
                f"Runtime error during script execution: {type(exc).__name__}: {exc}"
            )
            raise ScriptIngestionError(message) from exc
    except BaseException:
        # Execution failed: drop the temporary module so a failed ingestion does
        # not leak into sys.modules. On success it stays registered (see docstring).
        sys.modules.pop(module_name, None)
        raise

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


def _load_graph_from_namespace(
    namespace: dict[str, Any],
    entrypoint: str | None,
) -> StateGraph:
    """Resolve a StateGraph from an already-executed script namespace."""
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


def load_graph_from_script(
    source: str,
    *,
    entrypoint: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
    execution_timeout_seconds: float | None = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    script_filename: str | None = None,
) -> StateGraph:
    """Execute a LangGraph Python script and return the graph."""
    namespace = _execute_langgraph_script(
        source, max_script_bytes, execution_timeout_seconds, script_filename
    )
    try:
        return _load_graph_from_namespace(namespace, entrypoint)
    finally:
        # The script module is left registered by _execute_langgraph_script so
        # async entrypoints can resolve their state-schema type hints; remove it
        # now that the graph (and its channels) have been fully built.
        sys.modules.pop(namespace.get("__name__", ""), None)


def load_graph_from_script_full_env(
    source: str,
    *,
    entrypoint: str | None = None,
    execution_timeout_seconds: float | None = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    script_filename: str | None = None,
) -> StateGraph:
    """Execute a LangGraph script without size limits and return the graph."""
    namespace = _execute_langgraph_script(
        source, None, execution_timeout_seconds, script_filename
    )
    try:
        return _load_graph_from_namespace(namespace, entrypoint)
    finally:
        # See load_graph_from_script: drop the temporary module once the graph
        # has been resolved.
        sys.modules.pop(namespace.get("__name__", ""), None)


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
    """Execute ``awaitable`` on a dedicated thread to avoid loop nesting.

    The ingestion timeout is thread-scoped, so it cannot interrupt ``runner``.
    Bound the wait with whatever budget is left, and on timeout actually
    cancel the in-flight task through its own loop instead of abandoning the
    ``concurrent.futures.Future``: cancelling a future that has already
    started running is a no-op, so the awaitable would otherwise keep
    executing on an orphaned thread after ingestion has raised ``TimeoutError``.
    """
    loop = asyncio.new_event_loop()
    task_ready = threading.Event()
    task_box: list[asyncio.Task[Any]] = []

    def runner() -> Any:
        asyncio.set_event_loop(loop)
        task = loop.create_task(_await_awaitable(awaitable))
        task_box.append(task)
        task_ready.set()
        try:
            return loop.run_until_complete(task)
        finally:
            loop.close()

    timeout = remaining_execution_time()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(runner)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            if task_ready.wait(timeout=1):
                loop.call_soon_threadsafe(task_box[0].cancel)
            msg = "LangGraph script execution timed out"
            raise TimeoutError(msg) from exc
    finally:
        executor.shutdown(wait=False)


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


__all__ = ["load_graph_from_script", "load_graph_from_script_full_env"]
