"""Tests for the MicroPython-WASM runner against the real sandbox (Task 3.7)."""

from __future__ import annotations
import pytest
from orcheo.sandbox.exceptions import SandboxLimitError, SandboxOutputError
from orcheo.sandbox.runner import MicroPythonSandboxRunner


def _inputs(state: dict | None = None, configurable: dict | None = None) -> dict:
    """Build a minimal inputs envelope."""
    return {
        "state": state or {},
        "config": {"configurable": {}},
        "configurable": configurable or {},
    }


def test_runs_body_and_returns_update() -> None:
    """A successful body returns an ``update`` envelope."""
    runner = MicroPythonSandboxRunner()
    outputs = runner.run(
        "return {'doubled': self.factor * state['value']}",
        _inputs(state={"value": 21}, configurable={"factor": 2}),
        node_id="n",
    )

    assert outputs == {"update": {"doubled": 42}}


def test_body_exception_becomes_error_envelope() -> None:
    """A body that raises yields a structured ``error`` envelope (no host crash)."""
    runner = MicroPythonSandboxRunner()
    outputs = runner.run("raise ValueError('boom')", _inputs(), node_id="n")

    assert outputs["error"]["type"] == "ValueError"
    assert outputs["error"]["message"] == "boom"


def test_infinite_loop_hits_limit() -> None:
    """An infinite loop is stopped by the fuel/wall-clock limit."""
    runner = MicroPythonSandboxRunner(fuel=1_000_000, wall_timeout_seconds=2.0)
    with pytest.raises(SandboxLimitError):
        runner.run("while True:\n    x = 1", _inputs(), node_id="n")


def test_oversized_output_is_rejected() -> None:
    """Output exceeding the size limit raises a structured output error."""
    runner = MicroPythonSandboxRunner(max_output_bytes=512)
    with pytest.raises(SandboxOutputError, match="exceeded the maximum size"):
        runner.run("return {'big': 'x' * 5000}", _inputs(), node_id="n")


def test_non_json_output_is_rejected() -> None:
    """A non-JSON-coercible return value fails the run with an output error.

    MicroPython's ``json.dumps`` leniently emits a set as ``{2, 1}`` (invalid
    JSON), which the host then rejects when parsing the outputs envelope.
    """
    runner = MicroPythonSandboxRunner()
    with pytest.raises(SandboxOutputError, match="not valid JSON"):
        runner.run("return {'s': set([1, 2])}", _inputs(), node_id="n")


def test_describe_reports_pinned_version() -> None:
    """``describe`` surfaces the pinned artifact version and limits."""
    runner = MicroPythonSandboxRunner()
    described = runner.describe()

    assert described["package"] == "micropython-wasm"
    assert described["version"] == "0.1a2"
    assert described["fuel"] > 0
