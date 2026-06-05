"""One-shot script ingestion entrypoint executed only inside a sandbox."""

from __future__ import annotations
import json
import sys
from collections.abc import Mapping
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
        request = _read_request()
        payload = ingest_langgraph_script(
            str(request["source"]),
            entrypoint=request.get("entrypoint"),
            max_script_bytes=request.get("max_script_bytes", DEFAULT_SCRIPT_SIZE_LIMIT),
            execution_timeout_seconds=request.get(
                "execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT_SECONDS
            ),
        )
    except (KeyError, TypeError, ValueError, ScriptIngestionError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), flush=True)
        return
    except BaseException as exc:  # noqa: BLE001
        error = str(exc).strip()
        message = f"{type(exc).__name__}: {error}" if error else type(exc).__name__
        print(json.dumps({"status": "failed", "error": message}), flush=True)
        return
    # Strip fields the caller already has from the request so only the data
    # uniquely derived by running the script is transmitted through the
    # sandbox exec stream.
    _skip = {"source", "format", "entrypoint"}
    derived = {k: v for k, v in payload.items() if k not in _skip}
    print(json.dumps({"status": "succeeded", "payload": derived}), flush=True)


def _read_request() -> Mapping[str, Any]:
    """Read the complete JSON request body from stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("missing ingestion request")
    request = json.loads(raw)
    if not isinstance(request, Mapping):
        raise TypeError("ingestion request must be a JSON object")
    return request


if __name__ == "__main__":
    main()
