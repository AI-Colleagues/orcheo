"""Tests for the Sandbox Runtime Manager."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import pytest
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.errors import SandboxAcquireError, SandboxNotFoundError
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.models import SandboxState, WorkspaceRuntimePool
from orcheo.sandbox.runtime import InMemoryContainerRuntime


def _manager(
    *, pool_max: int = 2, pool_min: int = 0, idle_ttl: int = 900
) -> tuple[SandboxRuntimeManager, InMemoryContainerRuntime]:
    """Build a manager with an in-memory runtime and tight pool sizing."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(
        runtime=runtime,
        settings=SandboxSettings(
            default_pool_max=pool_max,
            default_pool_min=pool_min,
            default_idle_ttl_seconds=idle_ttl,
        ),
    )
    return manager, runtime


def test_acquire_provisions_and_marks_in_use() -> None:
    """Cold-acquire returns an IN_USE lease and starts a container."""
    manager, runtime = _manager()
    lease = manager.acquire("ws")
    assert lease.state is SandboxState.IN_USE
    assert lease.workspace_id == "ws"
    assert len(runtime.started) == 1
    assert runtime.started[0][1].labels["orcheo.workspace_id"] == "ws"


def test_release_returns_lease_to_pool_then_reuses() -> None:
    """After release, a subsequent acquire returns the warm lease."""
    manager, runtime = _manager(pool_max=1)
    lease = manager.acquire("ws")
    manager.release(lease)
    assert lease.state is SandboxState.READY
    reused = manager.acquire("ws")
    assert reused.lease_id == lease.lease_id
    assert len(runtime.started) == 1  # no new container started


def test_acquire_blocked_when_pool_exhausted() -> None:
    """Exceeding pool_max raises SandboxAcquireError."""
    manager, _ = _manager(pool_max=1)
    manager.acquire("ws")
    with pytest.raises(SandboxAcquireError):
        manager.acquire("ws")


def test_release_rejects_destroyed_lease() -> None:
    """A destroyed lease cannot be returned to the pool."""
    manager, _ = _manager()
    lease = manager.acquire("ws")
    manager.destroy(lease)
    with pytest.raises(SandboxNotFoundError):
        manager.release(lease)


def test_destroy_stops_container_and_clears_lease() -> None:
    """destroy() stops the container and removes the lease from tracking."""
    manager, runtime = _manager()
    lease = manager.acquire("ws")
    manager.destroy(lease)
    assert lease.state is SandboxState.DESTROYED
    assert len(runtime.stopped) == 1
    # Re-acquire should provision a fresh container.
    manager.acquire("ws")
    assert len(runtime.started) == 2


def test_acquire_rejects_blank_workspace() -> None:
    """workspace_id is mandatory."""
    manager, _ = _manager()
    with pytest.raises(SandboxAcquireError):
        manager.acquire("")


def test_reap_idle_destroys_expired_pooled_sandboxes() -> None:
    """Pooled sandboxes idle past TTL are destroyed."""
    manager, runtime = _manager(pool_max=2, idle_ttl=10)
    lease_a = manager.acquire("ws")
    lease_b = manager.acquire("ws")
    manager.release(lease_a)
    manager.release(lease_b)
    # Force one lease's last_used_at into the past.
    lease_a.last_used_at = datetime.now(tz=UTC) - timedelta(seconds=60)
    reaped = manager.reap_idle()
    assert reaped == [lease_a]
    assert len(runtime.stopped) == 1


def test_shutdown_destroys_all_outstanding_leases() -> None:
    """shutdown() destroys every tracked lease regardless of state."""
    manager, runtime = _manager(pool_max=3)
    lease_in_use = manager.acquire("ws")
    lease_pooled = manager.acquire("ws")
    manager.release(lease_pooled)
    manager.shutdown()
    assert len(runtime.stopped) == 2
    assert lease_in_use.state is SandboxState.DESTROYED
    assert lease_pooled.state is SandboxState.DESTROYED


def test_configure_workspace_overrides_defaults() -> None:
    """Custom pool config takes precedence over default settings."""
    manager, _ = _manager()
    manager.configure_workspace(
        WorkspaceRuntimePool(
            workspace_id="ws",
            pool_min=0,
            pool_max=5,
            cpu_limit="2",
            memory_limit="1g",
            pid_limit=512,
            scratch_disk_limit="2g",
            idle_ttl_seconds=120,
        )
    )
    pool = manager.get_workspace_pool("ws")
    assert pool.pool_max == 5
    assert pool.cpu_limit == "2"
