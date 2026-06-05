"""Tests for the sandbox ingestion runner entrypoint."""

from __future__ import annotations
import io
import json
import runpy

import pytest
from orcheo.sandbox import ingestion_runner


def test_main_succeeds_with_valid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner emits only derived fields; source/format/entrypoint are stripped."""
    full_payload = {
        "format": "langgraph-script",
        "source": "graph = object()",
        "entrypoint": "orcheo_workflow",
        "summary": {"nodes": [], "edges": [], "conditional_edges": []},
        "index": {"cron": [], "listeners": [], "mermaid": "graph TD;"},
    }
    monkeypatch.setattr(
        ingestion_runner, "ingest_langgraph_script", lambda *a, **kw: full_payload
    )
    monkeypatch.setattr(
        ingestion_runner.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "source": "graph = object()",
                    "entrypoint": "orcheo_workflow",
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
    # source, format and entrypoint are stripped — only derived data is emitted
    assert "source" not in result["payload"]
    assert "format" not in result["payload"]
    assert "entrypoint" not in result["payload"]
    assert result["payload"]["summary"] == full_payload["summary"]
    assert result["payload"]["index"] == full_payload["index"]


def test_main_includes_runner_token_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner echoes the service token only after ingestion completes."""
    full_payload = {
        "format": "langgraph-script",
        "source": "graph = object()",
        "entrypoint": None,
        "summary": {},
        "index": {},
    }
    monkeypatch.setattr(
        ingestion_runner, "ingest_langgraph_script", lambda *a, **kw: full_payload
    )
    monkeypatch.setattr(
        ingestion_runner.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "source": "graph = object()",
                    "entrypoint": None,
                    "_orcheo_runner_token": "runner-token",
                }
            )
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    ingestion_runner.main()

    result = json.loads(stdout.getvalue())
    assert result["status"] == "succeeded"
    assert result["runner_token"] == "runner-token"


def test_main_suppresses_tenant_printed_success_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant stdout is not allowed to share the runner result channel."""

    def _fake_ingest(*args: object, **kwargs: object) -> dict[str, object]:
        print('{"status":"succeeded","payload":{"summary":{},"index":{}}}')
        return {
            "format": "langgraph-script",
            "source": "graph = object()",
            "entrypoint": None,
            "summary": {"trusted": True},
            "index": {},
        }

    monkeypatch.setattr(ingestion_runner, "ingest_langgraph_script", _fake_ingest)
    monkeypatch.setattr(
        ingestion_runner.sys,
        "stdin",
        io.StringIO(json.dumps({"source": "graph = object()"})),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(ingestion_runner.sys, "stdout", stdout)

    ingestion_runner.main()

    lines = [line for line in stdout.getvalue().splitlines() if line]
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["status"] == "succeeded"
    assert result["payload"]["summary"] == {"trusted": True}


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
