"""Tests for the sandbox ingestion runner entrypoint."""

from __future__ import annotations
import io
import json

import pytest
from orcheo.sandbox import ingestion_runner


def test_ingestion_runner_serializes_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected process exits are converted into JSON failures."""

    def _raise(*args: object, **kwargs: object) -> object:
        raise SystemExit("bye")

    monkeypatch.setattr(ingestion_runner, "ingest_langgraph_script", _raise)
    monkeypatch.setattr(
        ingestion_runner.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "source": "graph = object()",
                    "entrypoint": None,
                    "max_script_bytes": 1,
                    "execution_timeout_seconds": 1.0,
                }
            )
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    ingestion_runner.main()

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "failed"
    assert payload["error"] == "SystemExit: bye"
