"""Shared constants for LangGraph ingestion."""

LANGGRAPH_SCRIPT_FORMAT = "langgraph-script"

# Graph format for restricted-mode workflow versions: a stored frozen IR.
FROZEN_IR_FORMAT = "frozen-ir"

# Maximum UTF-8 encoded size for LangGraph scripts submitted through the importer.
DEFAULT_SCRIPT_SIZE_LIMIT = 512 * 1024  # 512 KiB

# Maximum wall-clock time spent executing a LangGraph script during ingestion.
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60.0


__all__ = [
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_SCRIPT_SIZE_LIMIT",
    "FROZEN_IR_FORMAT",
    "LANGGRAPH_SCRIPT_FORMAT",
]
