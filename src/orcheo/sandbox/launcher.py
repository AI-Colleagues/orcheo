"""Launcher that runs agent processes inside a sandbox lease.

``SandboxedProcessLauncher`` is the integration point between
``ExternalAgentNode`` and the Sandbox Runtime Manager. The launcher:

1. Acquires a workspace sandbox via the manager.
2. Runs the CLI inside the sandbox via the runtime's exec primitive
   (``docker exec`` under the hood, or an HTTP call to the sandbox-runtime
   service via ``RemoteSandboxExec``).
3. Releases the sandbox to the warm pool when done.

There is no host fallback. Workspace runtime isolation is always on — any
caller that fails to provide a real ``_SandboxExec`` backend gets a
construction-time error so silently-unsandboxed execution is impossible.
"""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.external_agents.process import execute_process
from orcheo.sandbox.manager import SandboxRuntimeManager


class ProcessLauncher(Protocol):
    """Protocol satisfied by both the remote and in-sandbox launchers.

    Outside the sandbox (backend / worker) the bound launcher is a
    :class:`SandboxedProcessLauncher` that ``docker exec``s into a per-workspace
    sandbox container. Inside the sandbox (when ``WorkflowSandboxDispatcher``
    routes the entire workflow into a sandbox, and ``workflow_runner``
    spawns the graph), the bound launcher is a :class:`LocalProcessLauncher`
    that runs the CLI in-process — we are already isolated, so dispatching
    to *another* sandbox would be a recursion with no benefit.
    """

    async def run(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Run ``command`` on behalf of ``workspace_id``."""


class _SandboxExec(Protocol):
    """Protocol for executing a command inside a running sandbox."""

    async def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Execute ``command`` inside ``sandbox_id`` and return the result."""


class SandboxedProcessLauncher:
    """Run a one-shot process inside a per-workspace sandbox lease."""

    def __init__(
        self,
        manager: SandboxRuntimeManager,
        exec_backend: _SandboxExec,
    ) -> None:
        """Initialize the launcher.

        Args:
            manager: Sandbox Runtime Manager that owns lifecycles.
            exec_backend: How to run the command inside the sandbox. Required —
                there is no host fallback. Pass a ``RemoteSandboxExec`` for
                production or a test double for unit tests.
        """
        self._manager = manager
        self._exec = exec_backend

    async def run(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Acquire a workspace sandbox, run the command, release the sandbox."""
        lease = await asyncio.to_thread(self._manager.acquire, workspace_id)
        try:
            return await self._exec.exec(
                lease.sandbox_id,
                command,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            await asyncio.to_thread(self._manager.release, lease)


class LocalProcessLauncher:
    """Launcher that runs commands directly in the current process tree.

    Used by ``workflow_runner`` inside a sandbox container, where the workflow
    is already isolated and dispatching each CLI to *another* sandbox would
    add no isolation and a network hop. ``workspace_id`` is accepted (and
    ignored) so the launcher is interchangeable with
    :class:`SandboxedProcessLauncher`.
    """

    async def run(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Run ``command`` in this process tree via ``execute_process``."""
        del workspace_id
        return await execute_process(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
