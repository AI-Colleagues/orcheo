"""Context-managed dispatcher for the sandboxed agent-launch path.

Nodes call ``run_external_agent_process(...)`` instead of ``execute_process``
directly. When a ``SandboxedProcessLauncher`` is installed via
``use_launcher`` (a context manager), the call routes through the sandbox.
When no launcher is active — single-tenant deploys, unit tests, or self-
hosted setups with the feature flag off — the call falls straight through to
``execute_process`` so the legacy behavior is unchanged.

This keeps the change footprint on existing nodes tiny: one import + one
call swap.
"""

from __future__ import annotations
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.external_agents.process import execute_process
from orcheo.sandbox.launcher import SandboxedProcessLauncher


_active_launcher: ContextVar[SandboxedProcessLauncher | None] = ContextVar(
    "orcheo_sandbox_active_launcher", default=None
)


@contextmanager
def use_launcher(
    launcher: SandboxedProcessLauncher | None,
) -> Iterator[None]:
    """Bind ``launcher`` as the active sandbox dispatcher within this context."""
    token = _active_launcher.set(launcher)
    try:
        yield
    finally:
        _active_launcher.reset(token)


def get_active_launcher() -> SandboxedProcessLauncher | None:
    """Return the launcher bound to the current async context, if any."""
    return _active_launcher.get()


async def run_external_agent_process(
    command: list[str],
    *,
    workspace_id: str | None,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_seconds: float | int | None,
) -> ProcessExecutionResult:
    """Run an external agent's CLI through the sandbox launcher if active.

    Falls back to ``execute_process`` when no launcher is bound or
    ``workspace_id`` is missing (cannot scope a sandbox without it).
    """
    launcher = get_active_launcher()
    if launcher is None or not workspace_id:
        return await execute_process(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
    return await launcher.run(
        workspace_id=workspace_id,
        command=command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )
