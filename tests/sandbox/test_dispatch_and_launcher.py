"""Tests for the sandbox dispatcher and the agent-launcher integration."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from pathlib import Path
import pytest
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.dispatch import (
    SandboxDispatchError,
    get_active_launcher,
    run_external_agent_process,
    use_launcher,
)
from orcheo.sandbox.launcher import SandboxedProcessLauncher
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.runtime import InMemoryContainerRuntime


class _FakeExec:
    """Recording exec backend that returns a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        del cwd, env, timeout_seconds
        self.calls.append((sandbox_id, list(command)))
        return ProcessExecutionResult(
            command=command,
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.0,
        )


def test_no_launcher_active_raises() -> None:
    """Without an active launcher, dispatch fails closed — no host fallback."""
    with pytest.raises(SandboxDispatchError, match="No sandbox launcher"):
        asyncio.run(
            run_external_agent_process(
                ["echo", "hi"],
                workspace_id="ws",
                cwd=None,
                env=None,
                timeout_seconds=None,
            )
        )


def test_active_launcher_routes_through_sandbox() -> None:
    """When a launcher is bound, the dispatcher uses it."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(runtime=runtime)
    fake_exec = _FakeExec()
    launcher = SandboxedProcessLauncher(manager=manager, exec_backend=fake_exec)

    async def go() -> ProcessExecutionResult:
        with use_launcher(launcher):
            assert get_active_launcher() is launcher
            return await run_external_agent_process(
                ["agent-cli"],
                workspace_id="ws",
                cwd=None,
                env=None,
                timeout_seconds=None,
            )

    result = asyncio.run(go())
    assert result.exit_code == 0
    assert len(fake_exec.calls) == 1
    # A workspace sandbox should have been provisioned and released to the pool.
    started = runtime.started[0][1]
    assert started.labels["orcheo.workspace_id"] == "ws"
    assert runtime.stopped == [], (
        "sandbox should return to the warm pool, not be destroyed"
    )
    # Reacquiring the same workspace must hit the warm pool rather than start a new container.
    asyncio.run(asyncio.to_thread(manager.acquire, "ws"))
    assert len(runtime.started) == 1
    assert get_active_launcher() is None


def test_dispatcher_missing_workspace_id_raises() -> None:
    """Without a workspace_id, dispatch fails closed — no host fallback."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(runtime=runtime)
    launcher = SandboxedProcessLauncher(manager=manager, exec_backend=_FakeExec())

    async def go() -> ProcessExecutionResult:
        with use_launcher(launcher):
            return await run_external_agent_process(
                ["cmd"],
                workspace_id=None,
                cwd=None,
                env=None,
                timeout_seconds=None,
            )

    with pytest.raises(SandboxDispatchError, match="workspace_id is required"):
        asyncio.run(go())
    assert runtime.started == []
