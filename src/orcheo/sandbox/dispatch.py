"""Context-managed dispatcher for the sandboxed agent-launch path.

Nodes call ``run_external_agent_process(...)`` instead of spawning processes
directly. A ``SandboxedProcessLauncher`` must be bound via ``use_launcher``
before any agent CLI runs — workspace runtime isolation is always on, and
there is no host-fallback path. Calling without an active launcher or
without a workspace id is a programmer error and raises
``SandboxDispatchError``.
"""

from __future__ import annotations
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.errors import SandboxError
from orcheo.sandbox.launcher import SandboxedProcessLauncher


class SandboxDispatchError(SandboxError):
    """Raised when an agent process is dispatched without an active sandbox."""


_active_launcher: ContextVar[SandboxedProcessLauncher | None] = ContextVar(
    "orcheo_sandbox_active_launcher", default=None
)


@contextmanager
def use_launcher(launcher: SandboxedProcessLauncher) -> Iterator[None]:
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
    """Run an external agent's CLI through the active sandbox launcher.

    Raises ``SandboxDispatchError`` if no launcher is bound or
    ``workspace_id`` is missing — there is no host fallback.
    """
    launcher = get_active_launcher()
    if launcher is None:
        msg = (
            "No sandbox launcher bound for agent process dispatch. "
            "Wrap the call site in `with use_launcher(...)`."
        )
        raise SandboxDispatchError(msg)
    if not workspace_id:
        msg = "workspace_id is required to dispatch an agent process to a sandbox"
        raise SandboxDispatchError(msg)
    return await launcher.run(
        workspace_id=workspace_id,
        command=command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )
