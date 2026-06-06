"""Sandbox helpers used while loading LangGraph scripts."""

from __future__ import annotations
import ast
import builtins
import contextlib
import importlib
import operator
import signal
import sys
import threading
import time
from collections.abc import Callable, Generator
from functools import lru_cache
from pathlib import Path
from types import CodeType, FrameType, MappingProxyType
from typing import Any, cast
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
)
from RestrictedPython.transformer import RestrictingNodeTransformer
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.plugins.paths import build_storage_paths


class AsyncAllowingTransformer(RestrictingNodeTransformer):
    """Custom policy that allows async function definitions and common Python syntax.

    This extends RestrictedPython's standard transformer to allow:
    - AsyncFunctionDef statements (async functions)
    - Await expressions (await keyword)
    - AnnAssign statements (annotated assignments like PEP 526)
    - Reading the __name__ special variable (for if __name__ == "__main__" guards)
    """

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        """Allow async function definitions."""
        return self.node_contents_visit(node)

    def visit_Await(self, node: ast.Await) -> ast.Await:  # noqa: N802
        """Allow await expressions inside async functions."""
        return self.node_contents_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:  # noqa: N802
        """Allow annotated assignments (PEP 526)."""
        return self.node_contents_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.Name:  # noqa: N802
        """Allow reading the __name__ special variable."""
        if isinstance(node.ctx, ast.Load) and node.id == "__name__":
            return node
        return self.node_contents_visit(node)


TraceFunc = Callable[[FrameType | None, str, object], object]

_SAFE_MODULE_PREFIXES: tuple[str, ...] = (
    "asyncio",
    "base64",
    "csv",
    "json",
    "langgraph",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langchain_openai",
    "orcheo",
    "typing",
    "typing_extensions",
    "collections",
    "dataclasses",
    "datetime",
    "functools",
    "html",
    "itertools",
    "math",
    "operator",
    "pydantic",
    "re",
    "uuid",
)


def _plugin_site_packages() -> Path:
    """Return the managed plugin ``site-packages`` path for the active Python."""
    install_dir = Path(build_storage_paths().install_dir)
    return (
        install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def _ensure_plugin_sys_path() -> None:
    """Expose managed plugin packages to sandboxed workflow imports."""
    site_packages = _plugin_site_packages()
    condition = site_packages.exists() and str(site_packages) not in sys.path
    if condition:  # pragma: no branch
        sys.path.insert(0, str(site_packages))


def _resolve_compiler() -> Callable[[str, str, str], CodeType]:
    """Return the active RestrictedPython compiler, honoring monkeypatching."""
    ingestion_module = sys.modules.get("orcheo.graph.ingestion")
    compiler_fn: Callable[[str, str, str], CodeType] | None = None
    if ingestion_module is not None:
        compiler_candidate = getattr(ingestion_module, "compile_restricted", None)
        if callable(compiler_candidate):
            compiler_fn = cast(Callable[[str, str, str], CodeType], compiler_candidate)
    return compiler_fn or compile_restricted  # pragma: no cover


_INPLACE_OPERATORS: dict[str, Callable[[Any, Any], Any]] = {
    "+=": operator.iadd,
    "-=": operator.isub,
    "*=": operator.imul,
    "/=": operator.itruediv,
    "//=": operator.ifloordiv,
    "%=": operator.imod,
    "**=": operator.ipow,
    ">>=": operator.irshift,
    "<<=": operator.ilshift,
    "&=": operator.iand,
    "^=": operator.ixor,
    "|=": operator.ior,
    "@=": operator.imatmul,
}


def _inplacevar_(op: str, x: Any, y: Any) -> Any:
    """Implement augmented assignment for names (e.g. ``n += 1``)."""
    try:
        return _INPLACE_OPERATORS[op](x, y)
    except KeyError:  # pragma: no cover - defensive
        msg = f"Unsupported in-place operator '{op}' in LangGraph scripts"
        raise ScriptIngestionError(msg) from None


def create_sandbox_namespace() -> dict[str, Any]:
    """Return a namespace configured with restricted builtins for script exec."""

    def _restricted_import(
        name: str,
        globals_: dict[str, Any] | None = None,
        locals_: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        """Import ``name`` when it matches an allow-listed module prefix."""
        if level != 0:
            msg = "Relative imports are not supported in LangGraph scripts"
            raise ScriptIngestionError(msg)

        if not any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in _SAFE_MODULE_PREFIXES
        ) and not name.startswith("orcheo_plugin_"):
            msg = f"Import of module '{name}' is not permitted in LangGraph scripts"
            raise ScriptIngestionError(msg)

        if name.startswith("orcheo_plugin_"):
            _ensure_plugin_sys_path()

        module = importlib.import_module(name)
        return module

    builtin_snapshot = {name: value for name, value in safe_builtins.items()}
    builtin_snapshot.update(
        {
            "__build_class__": builtins.__build_class__,
            "__import__": _restricted_import,
            "property": property,
            "classmethod": classmethod,
            "staticmethod": staticmethod,
            "NotImplemented": NotImplemented,
            "Ellipsis": Ellipsis,
            "dict": dict,
            "list": list,
            "set": set,
            "any": builtins.any,
            "getattr": builtins.getattr,
            "reversed": builtins.reversed,
            "max": builtins.max,
            "min": builtins.min,
            "sum": builtins.sum,
            "enumerate": builtins.enumerate,
            "type": type,
        }
    )

    namespace: dict[str, Any] = {
        "__builtins__": MappingProxyType(builtin_snapshot),
        "__name__": "__orcheo_ingest__",
        "__package__": None,
        "__metaclass__": type,
        "_getattr_": getattr,
        "_getattr_static_": getattr,
        "_setattr_": setattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_print_": print,
        "_write_": full_write_guard,
        "_inplacevar_": _inplacevar_,
    }
    return namespace


@contextlib.contextmanager
def execution_timeout(
    timeout_seconds: float | None,
    *,
    sys_module: Any | None = None,
    threading_module: Any | None = None,
    time_module: Any | None = None,
) -> Generator[None, None, None]:
    """Enforce a wall-clock timeout around script execution."""
    if timeout_seconds is None or timeout_seconds <= 0:
        yield
        return

    sys_obj = sys_module or sys
    threading_obj = threading_module or threading
    time_obj = time_module or time

    use_signal = (
        hasattr(signal, "setitimer")
        and threading_obj.current_thread() is threading_obj.main_thread()
    )

    if use_signal:
        previous_handler = signal.getsignal(signal.SIGALRM)

        def _handle_timeout(_signum: int, _frame: FrameType | None) -> None:
            raise TimeoutError(
                "LangGraph script execution timed out"
            )  # pragma: no cover

        try:
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        return

    deadline = time_obj.perf_counter() + timeout_seconds

    def _trace_timeout(_frame: FrameType | None, event: str, _arg: object) -> TraceFunc:
        if event == "line" and time_obj.perf_counter() > deadline:
            raise TimeoutError("LangGraph script execution timed out")
        return _trace_timeout

    previous_trace = cast(TraceFunc | None, sys_obj.gettrace())
    previous_thread_trace = cast(TraceFunc | None, threading_obj.gettrace())

    sys_obj.settrace(cast(Any, _trace_timeout))
    threading_obj.settrace(cast(Any, _trace_timeout))
    try:
        yield
    finally:
        if previous_trace is None:
            sys_obj.settrace(cast(Any, None))
        else:
            sys_obj.settrace(cast(Any, previous_trace))

        if previous_thread_trace is None:
            threading_obj.settrace(cast(Any, None))
        else:
            threading_obj.settrace(cast(Any, previous_thread_trace))


@lru_cache(maxsize=128)
def compile_langgraph_script(source: str) -> CodeType:
    """Compile a LangGraph script under RestrictedPython with caching."""
    compiler_fn = _resolve_compiler()
    return compiler_fn(  # type: ignore[call-arg]
        source,
        "<langgraph-script>",
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        policy=AsyncAllowingTransformer,
    )


def validate_script_size(source: str, max_script_bytes: int | None) -> None:
    """Raise ``ScriptIngestionError`` when the script exceeds the byte limit."""
    if max_script_bytes is None:
        return

    if max_script_bytes <= 0:
        msg = "LangGraph script size limit must be a positive integer"
        raise ScriptIngestionError(msg)

    encoded_length = len(source.encode("utf-8"))
    if encoded_length > max_script_bytes:
        msg = f"LangGraph script exceeds the permitted size of {max_script_bytes} bytes"
        raise ScriptIngestionError(msg)


__all__ = [
    "AsyncAllowingTransformer",
    "compile_langgraph_script",
    "create_sandbox_namespace",
    "execution_timeout",
    "validate_script_size",
]
