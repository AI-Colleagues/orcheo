"""Launcher that runs agent processes inside a sandbox lease.

``SandboxedProcessLauncher`` is the integration point between the existing
``ExternalAgentNode`` (which currently spawns CLI processes directly via
``execute_process``) and the Sandbox Runtime Manager. The launcher:

1. Acquires an agent sandbox for the workspace.
2. Runs the CLI inside the sandbox via the runtime's exec primitive
   (``docker exec`` under the hood, or a fake in tests).
3. Destroys the sandbox on completion (agent sandboxes are single-use).

This module intentionally keeps the ``execute_process`` fallback intact:
when the feature flag is off, callers pass through directly to the legacy
path. Wiring in ``ExternalAgentNode`` lives in
``orcheo.nodes.external_agent``.
"""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.external_agents.process import execute_process
from orcheo.sandbox.manager import SandboxRuntimeManager


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


@dataclass
class HostFallbackExec:
    """Default exec that just runs the process on the host (legacy path)."""

    async def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Forward to the legacy ``execute_process`` helper."""
        del sandbox_id
        return await execute_process(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )


class SandboxedProcessLauncher:
    """Run a one-shot process inside a freshly-provisioned agent sandbox."""

    def __init__(
        self,
        manager: SandboxRuntimeManager,
        exec_backend: _SandboxExec | None = None,
    ) -> None:
        """Initialize the launcher.

        Args:
            manager: Sandbox Runtime Manager that owns lifecycles.
            exec_backend: How to run the command inside the sandbox. Defaults
                to ``HostFallbackExec`` for environments where the sandbox is
                a no-op (tests, single-tenant).
        """
        self._manager = manager
        self._exec = exec_backend or HostFallbackExec()

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
