"""Context-managed dispatcher for sandboxed process launch.

The active launcher is bound via ``use_launcher`` before any sandboxed
process runs. Calling without an active launcher is a programmer error
and raises ``SandboxDispatchError``.
"""

from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from orcheo.sandbox.errors import SandboxError
from orcheo.sandbox.launcher import ProcessLauncher


class SandboxDispatchError(SandboxError):
    """Raised when a process is dispatched without an active sandbox."""


_active_launcher: ContextVar[ProcessLauncher | None] = ContextVar(
    "orcheo_sandbox_active_launcher", default=None
)


@contextmanager
def use_launcher(launcher: ProcessLauncher) -> Iterator[None]:
    """Bind ``launcher`` as the active process dispatcher within this context."""
    token = _active_launcher.set(launcher)
    try:
        yield
    finally:
        _active_launcher.reset(token)


def get_active_launcher() -> ProcessLauncher | None:
    """Return the launcher bound to the current async context, if any."""
    return _active_launcher.get()
