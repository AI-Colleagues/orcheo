"""Public entrypoints for LangGraph script ingestion."""

from __future__ import annotations
from typing import Any
from orcheo.graph.ingestion.ast_extraction import extract_graph_index
from orcheo.graph.ingestion.config import (
    DEFAULT_SCRIPT_SIZE_LIMIT,
    LANGGRAPH_SCRIPT_FORMAT,
)
from orcheo.graph.ingestion.exceptions import ScriptIngestionError
from orcheo.graph.ingestion.loader import (
    load_graph_from_script,
    load_graph_from_script_full_env,
)
from orcheo.graph.ingestion.sandbox import (
    compile_langgraph_script,
    validate_script_size,
)


def ingest_langgraph_script(
    source: str,
    *,
    entrypoint: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
) -> dict[str, Any]:
    """Validate and index a LangGraph Python script without executing it.

    The returned payload stores the source alongside a lightweight index of
    derived metadata (cron triggers, listener subscriptions). Mermaid
    rendering is deferred to the dedicated endpoint which builds the graph
    on demand using the RP-sandboxed loader.
    """
    validate_script_size(source, max_script_bytes)
    try:
        compile_langgraph_script(source)
    except ScriptIngestionError:
        raise
    except SyntaxError as exc:
        raise ScriptIngestionError(f"Compilation error: {exc}") from exc

    index = extract_graph_index(source)
    return {
        "format": LANGGRAPH_SCRIPT_FORMAT,
        "source": source,
        "entrypoint": entrypoint,
        "index": index,
    }


__all__ = [
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "LANGGRAPH_SCRIPT_FORMAT",
    "ScriptIngestionError",
    "compile_langgraph_script",
    "extract_graph_index",
    "ingest_langgraph_script",
    "load_graph_from_script",
    "load_graph_from_script_full_env",
    "validate_script_size",
]
