"""Data models for the Sandbox Runtime Manager."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pydantic import BaseModel


class ProcessExecutionResult(BaseModel):
    """Captured result for a managed subprocess invocation."""

    command: list[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float


class SandboxState(str, Enum):
    """Lifecycle state of a sandbox lease."""

    PROVISIONING = "provisioning"
    READY = "ready"
    IN_USE = "in_use"
    RELEASED = "released"
    DESTROYED = "destroyed"


def _utcnow() -> datetime:
    """Return a timezone-aware current UTC timestamp."""
    return datetime.now(tz=UTC)


@dataclass
class SandboxLease:
    """In-memory record of a sandbox handed out to a caller.

    One sandbox per workspace hosts both vibe agent sessions and workflow
    runs; the lease has no per-kind distinction. Concurrency is bounded by
    ``WorkspaceRuntimePool.pool_max``.
    """

    lease_id: str
    workspace_id: str
    sandbox_id: str
    state: SandboxState = SandboxState.PROVISIONING
    created_at: datetime = field(default_factory=_utcnow)
    last_used_at: datetime = field(default_factory=_utcnow)

    def touch(self) -> None:
        """Update ``last_used_at`` to now (called on use)."""
        self.last_used_at = _utcnow()


@dataclass(frozen=True)
class WorkspaceRuntimePool:
    """Per-workspace warm-pool configuration."""

    workspace_id: str
    pool_min: int = 0
    pool_max: int = 4
    cpu_limit: str = "1.0"
    memory_limit: str = "512m"
    pid_limit: int = 256
    scratch_disk_limit: str = "1g"
    idle_ttl_seconds: int = 900

    def __post_init__(self) -> None:
        """Validate pool sizing invariants."""
        if self.pool_min < 0:
            msg = "pool_min must be >= 0"
            raise ValueError(msg)
        if self.pool_max < self.pool_min:
            msg = "pool_max must be >= pool_min"
            raise ValueError(msg)
        if self.pid_limit <= 0:
            msg = "pid_limit must be > 0"
            raise ValueError(msg)
        if self.idle_ttl_seconds <= 0:
            msg = "idle_ttl_seconds must be > 0"
            raise ValueError(msg)


@dataclass(frozen=True)
class SandboxAuditEvent:
    """An auditable lifecycle or policy event emitted by the sandbox runtime."""

    event: str
    workspace_id: str
    sandbox_id: str | None = None
    run_id: str | None = None
    detail: str = ""
    created_at: datetime = field(default_factory=_utcnow)

    def as_log_extra(self) -> dict[str, str]:
        """Return a flat ``logging.extra``-compatible dict for structured logs."""
        return {
            "sandbox_event": self.event,
            "workspace_id": self.workspace_id,
            "sandbox_id": self.sandbox_id or "",
            "run_id": self.run_id or "",
            "detail": self.detail,
            "created_at": self.created_at.isoformat(),
        }
