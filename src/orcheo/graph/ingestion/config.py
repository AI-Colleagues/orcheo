"""Shared constants for LangGraph ingestion."""

LANGGRAPH_SCRIPT_FORMAT = "langgraph-script"

# Maximum UTF-8 encoded size for LangGraph scripts submitted through the importer.
DEFAULT_SCRIPT_SIZE_LIMIT = 512 * 1024  # 512 KiB

# Maximum wall-clock time spent executing a LangGraph script during ingestion.
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60.0


__all__ = [
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "LANGGRAPH_SCRIPT_FORMAT",
]
