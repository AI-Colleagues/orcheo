"""Sandbox Runtime Manager: acquire/release/destroy sandbox leases.

This is the only component that owns the lifecycle of sandbox containers. The
Celery worker and the agent-launch path call ``acquire`` to obtain a
``SandboxLease`` for a workspace, execute their workload, then ``release`` to
return the sandbox to the warm pool, or ``destroy`` to tear it down.

There is **one sandbox kind per workspace** — the same container hosts vibe
agent sessions and workflow runs. Sandboxes are pooled within the bounds of
the workspace's ``WorkspaceRuntimePool``.
"""

from __future__ import annotations
import socket
import threading
import uuid
from collections import defaultdict, deque
from datetime import UTC, timedelta
from typing import Final
from urllib.parse import urlparse
from orcheo.sandbox.audit import SandboxAuditLogger
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.errors import (
    SandboxAcquireError,
    SandboxLifecycleError,
    SandboxNotFoundError,
)
from orcheo.sandbox.models import (
    SandboxAuditEvent,
    SandboxLease,
    SandboxState,
    WorkspaceRuntimePool,
)
from orcheo.sandbox.runtime import (
    ContainerHandle,
    ContainerRuntime,
    ContainerSpec,
)


_DEFAULT_NETWORK: Final[str] = "sandbox-egress"

# Sandbox containers run with a read-only rootfs (see ContainerSpec defaults)
# and only mount /scratch as writable tmpfs. Pinning the managed external-agent
# runtime root under /scratch lets ExternalAgentRuntimeManager mkdir its tree
# from inside the sandbox; without it the default ~/.orcheo path fails with
# EACCES against the read-only rootfs.
_SANDBOX_AGENT_RUNTIME_ROOT: Final[str] = "/scratch/agent-runtimes"


class SandboxRuntimeManager:
    """Provision, lease, and destroy per-workspace sandbox containers."""

    def __init__(
        self,
        runtime: ContainerRuntime,
        settings: SandboxSettings | None = None,
        audit_logger: SandboxAuditLogger | None = None,
    ) -> None:
        """Initialize the manager.

        Args:
            runtime: Container runtime backing the sandboxes.
            settings: Sandbox settings. Defaults to a fresh ``SandboxSettings()``.
            audit_logger: Audit-event sink. Defaults to a new
                ``SandboxAuditLogger`` using the configured logger name.
        """
        self._runtime = runtime
        self._settings = settings or SandboxSettings()
        self._audit = audit_logger or SandboxAuditLogger(
            self._settings.audit_logger_name
        )
        self._lock = threading.RLock()
        self._leases: dict[str, SandboxLease] = {}
        self._handles: dict[str, ContainerHandle] = {}
        self._pools: dict[str, deque[str]] = defaultdict(deque)
        self._workspace_configs: dict[str, WorkspaceRuntimePool] = {}

    @property
    def settings(self) -> SandboxSettings:
        """Return the active sandbox settings."""
        return self._settings

    def configure_workspace(self, pool: WorkspaceRuntimePool) -> None:
        """Register or replace a per-workspace pool configuration."""
        with self._lock:
            self._workspace_configs[pool.workspace_id] = pool

    def get_workspace_pool(self, workspace_id: str) -> WorkspaceRuntimePool:
        """Return the pool config for ``workspace_id``, falling back to defaults."""
        with self._lock:
            existing = self._workspace_configs.get(workspace_id)
            if existing is not None:
                return existing
            pool = WorkspaceRuntimePool(
                workspace_id=workspace_id,
                pool_min=self._settings.default_pool_min,
                pool_max=self._settings.default_pool_max,
                cpu_limit=self._settings.default_cpu_limit,
                memory_limit=self._settings.default_memory_limit,
                pid_limit=self._settings.default_pid_limit,
                scratch_disk_limit=self._settings.default_scratch_disk_limit,
                idle_ttl_seconds=self._settings.default_idle_ttl_seconds,
            )
            self._workspace_configs[workspace_id] = pool
            return pool

    def acquire(
        self,
        workspace_id: str,
        *,
        run_id: str | None = None,
    ) -> SandboxLease:
        """Acquire a sandbox lease for ``workspace_id``.

        Returns a warm pooled sandbox when one is available, otherwise cold-
        provisions a new container up to ``pool_max``. Raises
        ``SandboxAcquireError`` if the workspace's pool is exhausted.

        Args:
            workspace_id: Owning workspace.
            run_id: Optional workflow-run / session id; recorded in audit events.

        Returns:
            A ``SandboxLease`` in the ``IN_USE`` state.
        """
        if not workspace_id:
            msg = "workspace_id is required to acquire a sandbox"
            raise SandboxAcquireError(msg)

        pool = self.get_workspace_pool(workspace_id)

        with self._lock:
            lease = self._pop_from_pool(workspace_id)
            if lease is not None:
                lease.state = SandboxState.IN_USE
                lease.touch()
                self._audit.emit(
                    SandboxAuditEvent(
                        event="acquire_warm",
                        workspace_id=workspace_id,
                        sandbox_id=lease.sandbox_id,
                        run_id=run_id,
                    )
                )
                return lease
            in_use = self._count_in_use(workspace_id)
            if in_use >= pool.pool_max:
                msg = (
                    "Workspace sandbox pool exhausted "
                    f"({in_use}/{pool.pool_max} in use)"
                )
                raise SandboxAcquireError(msg)

            lease = self._provision(workspace_id, pool, run_id=run_id)
            lease.state = SandboxState.IN_USE
            return lease

    def release(self, lease: SandboxLease) -> None:
        """Return a lease to the workspace's warm pool."""
        with self._lock:
            self._require_known_lease(lease)
            if lease.state is SandboxState.DESTROYED:
                msg = "Cannot release a destroyed sandbox"
                raise SandboxLifecycleError(msg)
            pool = self.get_workspace_pool(lease.workspace_id)
            queue = self._pools[lease.workspace_id]
            queue.append(lease.lease_id)
            lease.state = SandboxState.READY
            lease.touch()
            self._audit.emit(
                SandboxAuditEvent(
                    event="release",
                    workspace_id=lease.workspace_id,
                    sandbox_id=lease.sandbox_id,
                    detail=(
                        f"pool_size={len(queue)} "
                        f"min={pool.pool_min} max={pool.pool_max}"
                    ),
                )
            )

    def destroy(self, lease: SandboxLease) -> None:
        """Tear down the sandbox and remove its lease record."""
        with self._lock:
            self._require_known_lease(lease)
            handle = self._handles.pop(lease.lease_id, None)
            queue = self._pools.get(lease.workspace_id)
            if queue is not None:
                try:
                    queue.remove(lease.lease_id)
                except ValueError:
                    pass
            lease.state = SandboxState.DESTROYED
            self._leases.pop(lease.lease_id, None)
        if handle is not None:
            try:
                self._runtime.stop(handle)
            finally:
                self._audit.emit(
                    SandboxAuditEvent(
                        event="destroy",
                        workspace_id=lease.workspace_id,
                        sandbox_id=lease.sandbox_id,
                    )
                )

    def reap_idle(self, *, now_seconds: float | None = None) -> list[SandboxLease]:
        """Destroy pooled sandboxes that have been idle past their TTL.

        Args:
            now_seconds: Override "now" in seconds-since-epoch (for tests).

        Returns:
            The leases that were destroyed.
        """
        from datetime import datetime

        reference = (
            datetime.fromtimestamp(now_seconds, tz=UTC)
            if now_seconds is not None
            else datetime.now(tz=UTC)
        )
        reaped: list[SandboxLease] = []
        with self._lock:
            for workspace_id, queue in list(self._pools.items()):
                pool = self.get_workspace_pool(workspace_id)
                ttl = timedelta(seconds=pool.idle_ttl_seconds)
                pruned: deque[str] = deque()
                for lease_id in queue:
                    lease = self._leases.get(lease_id)
                    if lease is None:
                        continue
                    if reference - lease.last_used_at >= ttl:
                        reaped.append(lease)
                    else:
                        pruned.append(lease_id)
                self._pools[workspace_id] = pruned
        for lease in reaped:
            self.destroy(lease)
        return reaped

    def shutdown(self) -> None:
        """Destroy every tracked lease — used on process exit."""
        with self._lock:
            leases = list(self._leases.values())
        for lease in leases:
            try:
                self.destroy(lease)
            except SandboxNotFoundError:
                continue

    def _pop_from_pool(self, workspace_id: str) -> SandboxLease | None:
        """Pop the oldest pooled lease, returning None if the pool is empty."""
        queue = self._pools[workspace_id]
        while queue:
            lease_id = queue.popleft()
            lease = self._leases.get(lease_id)
            if lease is not None and lease.state is SandboxState.READY:
                return lease
        return None

    def _count_in_use(self, workspace_id: str) -> int:
        """Count leases currently in use for ``workspace_id``."""
        return sum(
            1
            for lease in self._leases.values()
            if lease.workspace_id == workspace_id and lease.state is SandboxState.IN_USE
        )

    def _provision(
        self,
        workspace_id: str,
        pool: WorkspaceRuntimePool,
        *,
        run_id: str | None,
    ) -> SandboxLease:
        """Cold-provision a sandbox and register its lease."""
        spec = self._build_spec(workspace_id, pool)
        handle = self._runtime.start(spec)
        lease = SandboxLease(
            lease_id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            sandbox_id=handle.container_id,
            state=SandboxState.READY,
        )
        self._leases[lease.lease_id] = lease
        self._handles[lease.lease_id] = handle
        self._audit.emit(
            SandboxAuditEvent(
                event="provision",
                workspace_id=workspace_id,
                sandbox_id=lease.sandbox_id,
                run_id=run_id,
                detail=f"image={spec.image}",
            )
        )
        return lease

    def _build_spec(
        self,
        workspace_id: str,
        pool: WorkspaceRuntimePool,
    ) -> ContainerSpec:
        """Translate workspace config into a ``ContainerSpec``."""
        uid = _stable_uid(workspace_id)
        extra_hosts = self._resolve_extra_hosts()
        return ContainerSpec(
            image=self._settings.image,
            workspace_id=workspace_id,
            runtime=self._settings.container_runtime,
            environment=self._build_environment(),
            cpu_limit=pool.cpu_limit,
            memory_limit=pool.memory_limit,
            pid_limit=pool.pid_limit,
            scratch_size=pool.scratch_disk_limit,
            user=f"{uid}:{uid}",
            network_mode=_DEFAULT_NETWORK,
            labels={
                "orcheo.workspace_id": workspace_id,
            },
            dns=tuple(self._settings.sandbox_dns),
            extra_hosts=extra_hosts,
        )

    def _build_environment(self) -> dict[str, str]:
        """Build the env vars injected into every spawned sandbox.

        When ``egress_proxy_url`` is configured, also sets the standard
        ``HTTP_PROXY`` / ``HTTPS_PROXY`` triplet so HTTP clients in the
        sandbox route outbound HTTPS through the Envoy forward proxy. The
        credential broker host is added to ``NO_PROXY`` so credential calls
        don't go through the proxy (the proxy only allows tenant-allowlisted
        external hosts; the broker is internal).
        """
        env: dict[str, str] = {
            "ORCHEO_CREDENTIAL_BROKER_URL": self._settings.credential_broker_url,
            "ORCHEO_AGENT_RUNTIME_ROOT": _SANDBOX_AGENT_RUNTIME_ROOT,
        }
        proxy_url = self._settings.egress_proxy_url
        if proxy_url:
            no_proxy = ["localhost", "127.0.0.1"]
            broker_host = urlparse(self._settings.credential_broker_url).hostname
            if broker_host:
                no_proxy.append(broker_host)
            env.update(
                {
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                    "NO_PROXY": ",".join(no_proxy),
                }
            )
        return env

    def _resolve_extra_hosts(self) -> dict[str, str]:
        """Resolve in-cluster hostnames to static /etc/hosts entries.

        gVisor sandboxes cannot reach Docker's embedded DNS at 127.0.0.11, so
        Docker-network names (``sandbox-runtime``, ``egress-proxy``) won't
        resolve there even with ``sandbox_dns`` pointed at an upstream
        resolver — public DNS doesn't know those names. We resolve them from
        the manager's network namespace at spec-build time (the manager is a
        plain Compose service and can use Docker's resolver) and pin them
        into the child's ``/etc/hosts``.

        Raises:
            SandboxAcquireError: If a host can't be resolved — failing loud
                here surfaces the misconfiguration up-front instead of every
                workflow run dying deep inside the sandbox with an opaque
                connect error.
        """
        urls = (
            self._settings.credential_broker_url,
            self._settings.egress_proxy_url,
        )
        hosts: dict[str, str] = {}
        for url in urls:
            if not url:
                continue
            host = urlparse(url).hostname
            if host is None or _looks_like_ip(host) or host in hosts:
                continue
            try:
                hosts[host] = socket.gethostbyname(host)
            except OSError as exc:
                msg = (
                    f"Failed to resolve sandbox host {host!r} from the sandbox "
                    "manager. Sandboxes need a static /etc/hosts entry because "
                    "gVisor cannot reach Docker's embedded DNS. Verify the "
                    "host is reachable on the manager's network or use a "
                    "literal IP in the source URL."
                )
                raise SandboxAcquireError(msg) from exc
        return hosts

    def _require_known_lease(self, lease: SandboxLease) -> None:
        """Raise ``SandboxNotFoundError`` if the lease is not tracked."""
        if lease.lease_id not in self._leases:
            msg = f"Unknown sandbox lease: {lease.lease_id}"
            raise SandboxNotFoundError(msg)


def _looks_like_ip(host: str) -> bool:
    """Return True if ``host`` is already a literal IPv4/IPv6 address."""
    try:
        socket.inet_pton(socket.AF_INET, host)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except OSError:
        return False


def _stable_uid(workspace_id: str) -> int:
    """Derive a stable, non-root uid in [10000, 60000) from a workspace id.

    Using a per-tenant uid prevents one workspace's processes from signaling
    another's by uid even in the unlikely event of a sandbox escape that lands
    on the host kernel.
    """
    digest = sum(ord(c) for c in workspace_id) if workspace_id else 0
    return 10000 + (digest % 50000)
