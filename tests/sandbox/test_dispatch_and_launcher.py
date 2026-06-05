"""Tests for the sandbox dispatcher and the agent-launcher integration."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from pathlib import Path
import pytest
from orcheo.sandbox.models import ProcessExecutionResult
from orcheo.sandbox.dispatch import (
    SandboxDispatchError,
    get_active_launcher,
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


def test_no_active_launcher() -> None:
    """Without an active launcher, get_active_launcher returns None."""
    assert get_active_launcher() is None


def test_use_launcher_binds_and_unbinds() -> None:
    """use_launcher binds the launcher within context and clears it after."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(runtime=runtime)
    fake_exec = _FakeExec()
    launcher = SandboxedProcessLauncher(manager=manager, exec_backend=fake_exec)

    assert get_active_launcher() is None
    with use_launcher(launcher):
        assert get_active_launcher() is launcher
    assert get_active_launcher() is None


# ---------------------------------------------------------------------------
# LocalProcessLauncher tests (lines 128-129)
# ---------------------------------------------------------------------------


def test_local_process_launcher_runs_command_in_process_tree() -> None:
    """LocalProcessLauncher.run() executes the command via execute_process (lines 128-129)."""
    from orcheo.sandbox.launcher import LocalProcessLauncher

    results: list[ProcessExecutionResult] = []

    async def _fake_execute(
        command: list[str],
        *,
        cwd: object = None,
        env: object = None,
        timeout_seconds: object = None,
    ) -> ProcessExecutionResult:
        result = ProcessExecutionResult(
            command=command,
            stdout="hello",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.0,
        )
        results.append(result)
        return result

    async def go() -> ProcessExecutionResult:
        import orcheo.sandbox.launcher as launcher_module

        original = launcher_module.execute_process
        launcher_module.execute_process = _fake_execute  # type: ignore[assignment]
        try:
            launcher = LocalProcessLauncher()
            return await launcher.run(
                workspace_id="ws-ignored",
                command=["echo", "hello"],
                cwd=None,
                env={"FOO": "bar"},
                timeout_seconds=5.0,
            )
        finally:
            launcher_module.execute_process = original

    result = asyncio.run(go())
    assert result.stdout == "hello"
    assert result.exit_code == 0
    assert len(results) == 1
    assert results[0].command == ["echo", "hello"]
