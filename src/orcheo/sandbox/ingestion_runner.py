"""One-shot script ingestion entrypoint executed only inside a sandbox."""

from __future__ import annotations
import json
import sys
from typing import Any
from orcheo.graph.ingestion import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
    ScriptIngestionError,
    ingest_langgraph_script,
)


def main() -> None:
    """Read one ingestion request from stdin and emit one JSON result."""
    try:
        request: dict[str, Any] = json.loads(sys.stdin.readline())
        payload = ingest_langgraph_script(
            str(request["source"]),
            entrypoint=request.get("entrypoint"),
            max_script_bytes=request.get("max_script_bytes", DEFAULT_SCRIPT_SIZE_LIMIT),
            execution_timeout_seconds=request.get(
                "execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT_SECONDS
            ),
        )
    except (KeyError, TypeError, ValueError, ScriptIngestionError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return
    print(json.dumps({"status": "succeeded", "payload": payload}))


if __name__ == "__main__":
    main()
