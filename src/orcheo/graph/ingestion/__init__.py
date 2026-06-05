"""Public entrypoints for LangGraph script ingestion."""

from __future__ import annotations
from typing import Any
from orcheo.graph.ingestion.config import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
    LANGGRAPH_SCRIPT_FORMAT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.loader import (
    _compile_langgraph_script,
    _resolve_graph,
    load_graph_from_script,
)
from orcheo.graph.ingestion.loader import (
    execution_timeout as _execution_timeout,
)
from orcheo.graph.ingestion.loader import (
    validate_script_size as _validate_script_size,
)
from orcheo.graph.ingestion.summary import (
    _serialise_branch,
    _unwrap_runnable,
    summarise_graph_index,
    summarise_state_graph,
)


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


__all__ = [
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "LANGGRAPH_SCRIPT_FORMAT",
    "ScriptIngestionError",
    "_compile_langgraph_script",
    "_execution_timeout",
    "_resolve_graph",
    "_serialise_branch",
    "_unwrap_runnable",
    "_validate_script_size",
    "ingest_langgraph_script",
    "load_graph_from_script",
    "summarise_graph_index",
]
