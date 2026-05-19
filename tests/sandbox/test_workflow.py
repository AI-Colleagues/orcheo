"""Tests for the workflow dispatcher and runner."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from typing import Any
import pytest
from orcheo.sandbox.broker import CredentialBroker
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.models import SandboxLease, SandboxState
from orcheo.sandbox.runtime import InMemoryContainerRuntime
from orcheo.sandbox.workflow import (
    TRUSTED_NODE_TYPES,
    WorkflowRunResult,
    WorkflowRunSpec,
    WorkflowSandboxDispatcher,
    requires_sandbox,
)
from orcheo.sandbox.workflow_runner import run_in_subprocess


def _broker() -> CredentialBroker:
    return CredentialBroker(secret="s", resolver=lambda **_: "v")


def _manager() -> tuple[SandboxRuntimeManager, InMemoryContainerRuntime]:
    runtime = InMemoryContainerRuntime()
    return (
        SandboxRuntimeManager(
            runtime=runtime,
            settings=SandboxSettings(),
        ),
        runtime,
    )


class _RecordingRunner:
    """Runner that records calls and returns a configurable result."""

    def __init__(
        self,
        result: WorkflowRunResult | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[SandboxLease, WorkflowRunSpec, str]] = []
        self.result = result
        self.raise_on_call = raise_on_call

    async def execute(
        self,
        lease: SandboxLease,
        spec: WorkflowRunSpec,
        broker_token: str,
    ) -> WorkflowRunResult:
        self.calls.append((lease, spec, broker_token))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.result or WorkflowRunResult(
            run_id=spec.run_id,
            status="succeeded",
            outputs={"ok": True},
        )


def test_requires_sandbox_flags_unknown_node_types() -> None:
    """Any node outside the trusted set forces a sandbox route."""
    assert requires_sandbox(("AINode", "TenantPythonNode"))
    assert not requires_sandbox(tuple(TRUSTED_NODE_TYPES))


def test_dispatcher_routes_through_sandbox_by_default() -> None:
    """With fast-path off, every run acquires a workflow sandbox."""
    manager, runtime = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
    )
    spec = WorkflowRunSpec(
        run_id="r1",
        workspace_id="ws",
        workflow_definition={"nodes": []},
        inputs={"x": 1},
        node_types=("AINode",),
    )
    result = asyncio.run(dispatcher.dispatch(spec))
    assert result.status == "succeeded"
    assert len(runtime.started) == 1
    assert runner.calls[0][1].run_id == "r1"
    assert runner.calls[0][2]  # broker token populated


def test_dispatcher_fast_path_skips_sandbox_for_trusted_only() -> None:
    """Trusted-only workflows skip the sandbox when fast-path is enabled."""
    manager, runtime = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
        allow_in_worker_fast_path=True,
    )
    spec = WorkflowRunSpec(
        run_id="r2",
        workspace_id="ws",
        workflow_definition={"nodes": []},
        inputs={},
        node_types=("AINode", "TaskNode"),
    )
    asyncio.run(dispatcher.dispatch(spec))
    assert runtime.started == []
    assert runner.calls[0][2] == ""  # no broker token for fast path


def test_dispatcher_releases_sandbox_and_revokes_token_on_runner_error() -> None:
    """A runner exception still releases the sandbox and revokes the token."""
    manager, runtime = _manager()
    broker = _broker()
    runner = _RecordingRunner(raise_on_call=RuntimeError("boom"))
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager, runner=runner, broker=broker
    )
    spec = WorkflowRunSpec(
        run_id="r3",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("UnknownNode",),
    )
    result = asyncio.run(dispatcher.dispatch(spec))
    assert result.status == "failed"
    assert result.error == "boom"
    assert len(runtime.started) == 1
    # The sandbox should have been released back to the pool, not destroyed.
    assert len(runtime.stopped) == 0
    # The broker should have marked the run as revoked.
    token = broker.issue(workspace_id="ws", run_id="r3")
    # The previously issued token had a different timestamp; verify by issuing
    # then trying to parse — revocation is keyed by run_id, so any future use
    # of this run_id is rejected.
    with pytest.raises(Exception):
        broker.parse(token)


def test_run_in_subprocess_returns_failure_for_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure in the run-graph implementation is surfaced cleanly."""

    def _fail(
        definition: Mapping[str, Any], inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        del definition, inputs
        raise RuntimeError("graph blew up")

    monkeypatch.setattr("orcheo.sandbox.workflow_runner._run_graph", _fail)
    result = run_in_subprocess({}, {}, spawn=False)
    assert result["status"] == "failed"
    assert "graph blew up" in result["error"]


def test_run_in_subprocess_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run produces a succeeded result with outputs."""

    def _ok(
        definition: Mapping[str, Any], inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        del definition
        return {"sum": inputs["a"] + inputs["b"]}

    monkeypatch.setattr("orcheo.sandbox.workflow_runner._run_graph", _ok)
    result = run_in_subprocess({}, {"a": 2, "b": 3}, spawn=False)
    assert result["status"] == "succeeded"
    assert result["outputs"]["sum"] == 5


def test_should_sandbox_obeys_fast_path_flag() -> None:
    """should_sandbox returns True when fast path is disabled."""
    manager, _ = _manager()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=_RecordingRunner(),
        broker=_broker(),
        allow_in_worker_fast_path=False,
    )
    spec = WorkflowRunSpec(
        run_id="r4",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("AINode",),
    )
    assert dispatcher.should_sandbox(spec)


def test_synthetic_lease_used_when_fast_path_engages() -> None:
    """Fast path uses an in-worker synthetic lease so the runner contract holds."""
    manager, _ = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
        allow_in_worker_fast_path=True,
    )
    spec = WorkflowRunSpec(
        run_id="r5",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("TaskNode",),
    )
    asyncio.run(dispatcher.dispatch(spec))
    lease = runner.calls[0][0]
    assert lease.sandbox_id == "in-worker"
    assert lease.state is SandboxState.IN_USE
