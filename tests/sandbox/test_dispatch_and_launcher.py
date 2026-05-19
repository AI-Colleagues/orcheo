"""Tests for the sandbox dispatcher and the agent-launcher integration."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from pathlib import Path
import pytest
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.dispatch import (
    get_active_launcher,
    run_external_agent_process,
    use_launcher,
)
from orcheo.sandbox.launcher import HostFallbackExec, SandboxedProcessLauncher
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


def test_no_launcher_active_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no launcher bound, run_external_agent_process calls execute_process."""

    async def fake_execute(
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        del cwd, env, timeout_seconds
        return ProcessExecutionResult(
            command=command,
            stdout="legacy",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.0,
        )

    monkeypatch.setattr(
        "orcheo.sandbox.dispatch.execute_process",
        fake_execute,
    )
    result = asyncio.run(
        run_external_agent_process(
            ["echo", "hi"],
            workspace_id="ws",
            cwd=None,
            env=None,
            timeout_seconds=None,
        )
    )
    assert result.stdout == "legacy"


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


def test_dispatcher_falls_back_when_workspace_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a workspace_id we cannot scope a sandbox — fall through."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(runtime=runtime)
    launcher = SandboxedProcessLauncher(manager=manager, exec_backend=_FakeExec())

    async def fake_execute(
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        del cwd, env, timeout_seconds
        return ProcessExecutionResult(
            command=command,
            stdout="fallback",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.0,
        )

    monkeypatch.setattr("orcheo.sandbox.dispatch.execute_process", fake_execute)

    async def go() -> ProcessExecutionResult:
        with use_launcher(launcher):
            return await run_external_agent_process(
                ["cmd"],
                workspace_id=None,
                cwd=None,
                env=None,
                timeout_seconds=None,
            )

    result = asyncio.run(go())
    # No sandbox container was started.
    assert runtime.started == []
    assert result.stdout == "fallback"


def test_host_fallback_exec_uses_real_execute_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HostFallbackExec proxies straight to execute_process."""

    async def fake_execute(
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        del cwd, env, timeout_seconds
        return ProcessExecutionResult(
            command=command,
            stdout="x",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.0,
        )

    monkeypatch.setattr(
        "orcheo.sandbox.launcher.execute_process",
        fake_execute,
    )
    backend = HostFallbackExec()
    result = asyncio.run(
        backend.exec(
            "sb-1",
            ["true"],
            cwd=None,
            env=None,
            timeout_seconds=None,
        )
    )
    assert result.exit_code == 0
