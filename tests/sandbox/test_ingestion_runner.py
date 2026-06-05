"""Tests for the sandbox ingestion runner entrypoint."""

from __future__ import annotations
import io
import json
import runpy

import pytest
from orcheo.sandbox import ingestion_runner


def test_main_succeeds_with_valid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() emits succeeded JSON when ingestion completes (line 36)."""
    expected_payload = {"format": "langgraph-script"}
    monkeypatch.setattr(
        ingestion_runner, "ingest_langgraph_script", lambda *a, **kw: expected_payload
    )
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

    result = json.loads(stdout.getvalue())
    assert result["status"] == "succeeded"
    assert result["payload"] == expected_payload


def test_main_fails_with_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() emits a failed result when stdin is empty (lines 29-30, 43)."""
    monkeypatch.setattr(ingestion_runner.sys, "stdin", io.StringIO(""))
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    ingestion_runner.main()

    result = json.loads(stdout.getvalue())
    assert result["status"] == "failed"
    assert "missing" in result["error"]


def test_main_fails_with_non_mapping_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() emits a failed result when JSON root is not an object (lines 29-30, 46)."""
    monkeypatch.setattr(ingestion_runner.sys, "stdin", io.StringIO("[1, 2, 3]"))
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    ingestion_runner.main()

    result = json.loads(stdout.getvalue())
    assert result["status"] == "failed"
    assert "JSON object" in result["error"]


def test_ingestion_runner_main_block_is_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The if __name__ == '__main__' guard calls main() when executed as a module (line 51)."""
    import sys as _sys

    monkeypatch.setattr(ingestion_runner.sys, "stdin", io.StringIO(""))
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    # Remove the already-imported module so runpy executes a clean copy without
    # triggering the "found in sys.modules" RuntimeWarning.
    saved = _sys.modules.pop("orcheo.sandbox.ingestion_runner", None)
    try:
        runpy.run_module(
            "orcheo.sandbox.ingestion_runner", run_name="__main__", alter_sys=False
        )
    finally:
        if saved is not None:
            _sys.modules["orcheo.sandbox.ingestion_runner"] = saved

    result = json.loads(stdout.getvalue())
    assert result["status"] == "failed"
    assert "missing" in result["error"]


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
