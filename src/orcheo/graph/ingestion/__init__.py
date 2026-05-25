"""Public entrypoints for LangGraph script ingestion."""

from __future__ import annotations
import sys as _sys_module
import threading as _threading_module
import time as _time_module
from contextlib import AbstractContextManager
from typing import Any
from RestrictedPython import compile_restricted as _restricted_compile_restricted
from orcheo.graph.ingestion.config import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
    LANGGRAPH_SCRIPT_FORMAT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.loader import _resolve_graph as _loader_resolve_graph
from orcheo.graph.ingestion.loader import load_graph_from_script
from orcheo.graph.ingestion.sandbox import (
    compile_langgraph_script as _sandbox_compile_langgraph_script,
)
from orcheo.graph.ingestion.sandbox import (
    execution_timeout as _sandbox_execution_timeout,
)
from orcheo.graph.ingestion.sandbox import (
    validate_script_size as _sandbox_validate_script_size,
)
from orcheo.graph.ingestion.summary import (
    _serialise_branch as _summary_serialise_branch,
)
from orcheo.graph.ingestion.summary import _unwrap_runnable as _summary_unwrap_runnable
from orcheo.graph.ingestion.summary import (
    summarise_graph_index,
    summarise_state_graph,
)


_compile_langgraph_script = _sandbox_compile_langgraph_script
_resolve_graph = _loader_resolve_graph
_serialise_branch = _summary_serialise_branch
_unwrap_runnable = _summary_unwrap_runnable
_validate_script_size = _sandbox_validate_script_size
compile_restricted = _restricted_compile_restricted
sys = _sys_module
threading = _threading_module
time = _time_module


def ingest_langgraph_script(
    source: str,
    *,
    entrypoint: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
    execution_timeout_seconds: float | None = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return a workflow graph payload produced from a LangGraph Python script.

    The returned payload embeds the original script alongside a compact index
    containing derived metadata (for example, Mermaid output and cron trigger
    fields) while the original script is required to faithfully rebuild the
    graph during execution.
    """
    graph = load_graph_from_script(
        source,
        entrypoint=entrypoint,
        max_script_bytes=max_script_bytes,
        execution_timeout_seconds=execution_timeout_seconds,
    )
    summary = summarise_state_graph(graph)
    index = summarise_graph_index(graph)
    return {
        "format": LANGGRAPH_SCRIPT_FORMAT,
        "source": source,
        "entrypoint": entrypoint,
        "summary": summary,
        "index": index,
    }


def _execution_timeout(timeout_seconds: float | None) -> AbstractContextManager[None]:
    """Expose execution timeout that honours module-level monkeypatching."""
    return _sandbox_execution_timeout(
        timeout_seconds,
        sys_module=sys,
        threading_module=threading,
        time_module=time,
    )


__all__ = [
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "LANGGRAPH_SCRIPT_FORMAT",
    "ScriptIngestionError",
    "compile_restricted",
    "_compile_langgraph_script",
    "_execution_timeout",
    "_resolve_graph",
    "_serialise_branch",
    "_unwrap_runnable",
    "_validate_script_size",
    "sys",
    "threading",
    "time",
    "ingest_langgraph_script",
    "load_graph_from_script",
    "summarise_graph_index",
]
