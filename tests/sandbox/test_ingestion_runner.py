"""Tests for the one-shot sandbox ingestion process entrypoint."""

from __future__ import annotations

import io
import json
import runpy
import sys

import pytest

from orcheo.graph import ingestion as graph_ingestion
from orcheo.graph.ingestion import DEFAULT_SCRIPT_SIZE_LIMIT
from orcheo.sandbox import ingestion_runner


_VALID_SCRIPT = """
from langgraph.graph import StateGraph
from orcheo.graph.state import State

def build_graph():
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.set_entry_point("noop")
    graph.set_finish_point("noop")
    return graph
"""


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, object],
) -> dict[str, object]:
    monkeypatch.setattr(ingestion_runner.sys, "stdin", io.StringIO(json.dumps(payload)))
    ingestion_runner.main()
    return json.loads(capsys.readouterr().out)


def test_ingestion_runner_returns_serialized_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid source file returns the existing stored graph format."""
    result = _invoke(
        monkeypatch,
        capsys,
        {"source": _VALID_SCRIPT, "entrypoint": "build_graph"},
    )
    assert result["status"] == "succeeded"
    assert result["payload"]["source"] == _VALID_SCRIPT  # type: ignore[index]


def test_ingestion_runner_reports_syntax_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid Python is returned as validation failure, not an exception."""
    result = _invoke(
        monkeypatch,
        capsys,
        {"source": "not python !!!", "entrypoint": "build_graph"},
    )
    assert result["status"] == "failed"


def test_ingestion_runner_rejects_oversized_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one-shot path enforces the configured input size cap."""
    result = _invoke(
        monkeypatch,
        capsys,
        {"source": "x" * (DEFAULT_SCRIPT_SIZE_LIMIT + 1)},
    )
    assert result["status"] == "failed"
    assert "exceeds the permitted size" in str(result["error"])


def test_ingestion_runner_enforces_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Long-running source execution is bounded inside the process."""
    result = _invoke(
        monkeypatch,
        capsys,
        {"source": "while True:\n    pass\n", "execution_timeout_seconds": 0.01},
    )
    assert result["status"] == "failed"
    assert "execution exceeded" in str(result["error"])


def test_ingestion_runner_module_entrypoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Executing the module as a script still routes through main()."""

    def fake_ingest(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"graph": "ok"}

    monkeypatch.setattr(graph_ingestion, "ingest_langgraph_script", fake_ingest)
    monkeypatch.setattr(
        ingestion_runner.sys,
        "stdin",
        io.StringIO(json.dumps({"source": _VALID_SCRIPT, "entrypoint": "build_graph"})),
    )
    module_name = "orcheo.sandbox.ingestion_runner"
    loaded_module = sys.modules.pop(module_name, None)

    try:
        runpy.run_module(module_name, run_name="__main__")
    finally:
        if loaded_module is not None:
            sys.modules[module_name] = loaded_module

    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "succeeded", "payload": {"graph": "ok"}}
