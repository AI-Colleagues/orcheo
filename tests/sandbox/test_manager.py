"""Tests for the Sandbox Runtime Manager."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import pytest
from orcheo.sandbox import manager as manager_module
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.errors import (
    SandboxAcquireError,
    SandboxLifecycleError,
    SandboxNotFoundError,
)
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.models import SandboxLease, SandboxState, WorkspaceRuntimePool
from orcheo.sandbox.runtime import ContainerSpec, InMemoryContainerRuntime


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
    spec = runtime.started[0][1]
    assert spec.labels["orcheo.workspace_id"] == "ws"
    assert spec.environment == {
        "ORCHEO_CREDENTIAL_BROKER_URL": (
            "http://credential-relay:9091/credentials/resolve"
        ),
        "ORCHEO_AGENT_RUNTIME_ROOT": "/scratch/agent-runtimes",
    }
    assert spec.dns == ()
    assert spec.extra_hosts == {"credential-relay": "127.0.0.1"}


def test_acquire_fails_loud_when_broker_host_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DNS failure at spec-build time surfaces as SandboxAcquireError.

    Swallowing this would reproduce the original prod symptom: every workflow
    run dying inside the sandbox with an opaque httpx connect error.
    """

    def _raise(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr(manager_module.socket, "gethostbyname", _raise)
    manager, _ = _manager()
    with pytest.raises(SandboxAcquireError, match="sandbox host 'credential-relay'"):
        manager.acquire("ws")


def test_acquire_pins_and_proxies_through_egress_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When egress_proxy_url is set, the sandbox gets HTTP(S)_PROXY env vars
    and the proxy hostname is pinned in /etc/hosts.

    NO_PROXY must include the broker host so credential calls bypass the
    forward proxy (the proxy only allows tenant-allowlisted external hosts
    and would otherwise reject the internal broker).
    """
    # Different stub per host so we can tell the two entries apart.
    monkeypatch.setattr(
        manager_module.socket,
        "gethostbyname",
        lambda host: {"credential-relay": "10.0.0.7", "egress-proxy": "10.0.0.8"}[host],
    )
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(
        runtime=runtime,
        settings=SandboxSettings(egress_proxy_url="http://egress-proxy:3128"),
    )
    manager.acquire("ws")
    spec = runtime.started[0][1]
    assert spec.extra_hosts == {
        "credential-relay": "10.0.0.7",
        "egress-proxy": "10.0.0.8",
    }
    assert spec.environment["HTTP_PROXY"] == "http://egress-proxy:3128"
    assert spec.environment["HTTPS_PROXY"] == "http://egress-proxy:3128"
    assert spec.environment["NO_PROXY"] == "localhost,127.0.0.1,credential-relay"


def test_acquire_proxy_env_handles_broker_url_without_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker URL without a hostname falls back to a NO_PROXY without it.

    ``urlparse("http:///path").hostname`` is None — defensive branch that
    keeps NO_PROXY well-formed instead of appending ``"None"``.
    """
    monkeypatch.setattr(
        manager_module.socket,
        "gethostbyname",
        lambda host: "10.0.0.8",
    )
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(
        runtime=runtime,
        settings=SandboxSettings(
            credential_broker_url="http:///credentials/resolve",
            egress_proxy_url="http://egress-proxy:3128",
        ),
    )
    manager.acquire("ws")
    spec = runtime.started[0][1]
    assert spec.environment["NO_PROXY"] == "localhost,127.0.0.1"


def test_acquire_omits_proxy_env_when_egress_unset() -> None:
    """Without egress_proxy_url, no proxy env vars are injected."""
    manager, runtime = _manager()
    manager.acquire("ws")
    spec = runtime.started[0][1]
    assert "HTTP_PROXY" not in spec.environment
    assert "HTTPS_PROXY" not in spec.environment
    assert "NO_PROXY" not in spec.environment


def test_acquire_fails_loud_when_egress_proxy_host_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DNS failure on the egress-proxy host also surfaces fail-loud."""

    def _raise_for_proxy(host: str) -> str:
        if host == "egress-proxy":
            raise OSError("nope")
        return "127.0.0.1"

    monkeypatch.setattr(manager_module.socket, "gethostbyname", _raise_for_proxy)
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(
        runtime=runtime,
        settings=SandboxSettings(egress_proxy_url="http://egress-proxy:3128"),
    )
    with pytest.raises(SandboxAcquireError, match="sandbox host 'egress-proxy'"):
        manager.acquire("ws")


@pytest.mark.parametrize(
    "broker_url",
    [
        "http://10.0.0.5:9090/credentials/resolve",
        "http://[2001:db8::1]:9090/credentials/resolve",
    ],
    ids=["ipv4-literal", "ipv6-literal"],
)
def test_acquire_skips_hosts_entry_when_broker_url_is_ip(
    monkeypatch: pytest.MonkeyPatch,
    broker_url: str,
) -> None:
    """If the broker URL is already an IP literal, no /etc/hosts entry is needed."""
    runtime = InMemoryContainerRuntime()
    manager = SandboxRuntimeManager(
        runtime=runtime,
        settings=SandboxSettings(credential_broker_url=broker_url),
    )
    # If the manager tried to resolve, the stub above would have run; force a
    # raise so an accidental call shows up as a test failure.
    monkeypatch.setattr(
        manager_module.socket,
        "gethostbyname",
        lambda host: (_ for _ in ()).throw(AssertionError("should not resolve")),
    )
    manager.acquire("ws")
    spec = runtime.started[0][1]
    assert spec.extra_hosts == {}


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


def test_settings_property_returns_active_settings() -> None:
    """The settings property exposes the active SandboxSettings instance."""
    runtime = InMemoryContainerRuntime()
    settings = SandboxSettings(default_pool_max=3)
    manager = SandboxRuntimeManager(runtime=runtime, settings=settings)
    assert manager.settings is settings
    assert manager.settings.default_pool_max == 3


def test_release_raises_lifecycle_error_for_destroyed_tracked_lease() -> None:
    """release() raises SandboxLifecycleError when the lease is DESTROYED but tracked."""
    manager, _ = _manager()
    lease = manager.acquire("ws")
    # Force DESTROYED state without removing from _leases (simulates an edge case).
    lease.state = SandboxState.DESTROYED
    with pytest.raises(SandboxLifecycleError, match="Cannot release"):
        manager.release(lease)


def test_destroy_when_workspace_has_no_pool_entry() -> None:
    """destroy() handles leases whose workspace never had a pool queue."""
    manager, runtime = _manager()
    # Bypass acquire() to create a lease without initialising the pool deque.
    handle = runtime.start(ContainerSpec(image="img", workspace_id="ws-new"))
    lease = SandboxLease(
        lease_id="manual-id",
        workspace_id="ws-new",
        sandbox_id=handle.container_id,
        state=SandboxState.IN_USE,
    )
    manager._leases["manual-id"] = lease
    manager._handles["manual-id"] = handle
    manager.destroy(lease)
    assert lease.state is SandboxState.DESTROYED
    assert len(runtime.stopped) == 1


def test_destroy_when_handle_is_absent() -> None:
    """destroy() skips stop() when the lease has no associated container handle."""
    manager, runtime = _manager()
    # Add a lease to _leases only — no handle, no pool entry.
    lease = SandboxLease(
        lease_id="no-handle-id",
        workspace_id="ws-no-handle",
        sandbox_id="ghost",
        state=SandboxState.IN_USE,
    )
    manager._leases["no-handle-id"] = lease
    manager.destroy(lease)
    assert lease.state is SandboxState.DESTROYED
    # The runtime should not have been asked to stop anything.
    assert len(runtime.stopped) == 0


def test_reap_idle_skips_stale_pool_entries_with_missing_lease() -> None:
    """reap_idle() skips pool entries whose lease record no longer exists."""
    manager, runtime = _manager(pool_max=2, idle_ttl=10)
    lease = manager.acquire("ws")
    manager.release(lease)
    # Manually remove the lease record while keeping its id in the pool queue.
    del manager._leases[lease.lease_id]
    reaped = manager.reap_idle()
    assert reaped == []
    assert len(runtime.stopped) == 0


def test_shutdown_continues_past_already_destroyed_leases() -> None:
    """shutdown() skips leases that raise SandboxNotFoundError during destroy()."""
    manager, runtime = _manager(pool_max=2)
    lease1 = manager.acquire("ws")
    lease2 = manager.acquire("ws")
    # Monkey-patch destroy to raise SandboxNotFoundError for lease1.
    original_destroy = manager.destroy

    def patched_destroy(lease: SandboxLease) -> None:
        if lease.lease_id == lease1.lease_id:
            raise SandboxNotFoundError("already gone")
        original_destroy(lease)

    manager.destroy = patched_destroy  # type: ignore[method-assign]
    manager.shutdown()
    # lease2 should still be destroyed despite lease1 raising.
    assert lease2.state is SandboxState.DESTROYED


def test_pop_from_pool_skips_non_ready_lease() -> None:
    """_pop_from_pool() ignores pool entries whose lease is not in READY state."""
    manager, _ = _manager(pool_max=2)
    lease = manager.acquire("ws")
    # Manually push an IN_USE lease_id into the pool queue without marking it READY.
    manager._pools["ws"].append(lease.lease_id)
    # The pool has one entry, but it is IN_USE — should be skipped.
    popped = manager._pop_from_pool("ws")
    assert popped is None
