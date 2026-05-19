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
import threading
import uuid
from collections import defaultdict, deque
from datetime import UTC, timedelta
from typing import Final
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
        return ContainerSpec(
            image=self._settings.image,
            workspace_id=workspace_id,
            runtime=self._settings.container_runtime,
            cpu_limit=pool.cpu_limit,
            memory_limit=pool.memory_limit,
            pid_limit=pool.pid_limit,
            scratch_size=pool.scratch_disk_limit,
            user=f"{uid}:{uid}",
            network_mode=_DEFAULT_NETWORK,
            labels={
                "orcheo.workspace_id": workspace_id,
            },
        )

    def _require_known_lease(self, lease: SandboxLease) -> None:
        """Raise ``SandboxNotFoundError`` if the lease is not tracked."""
        if lease.lease_id not in self._leases:
            msg = f"Unknown sandbox lease: {lease.lease_id}"
            raise SandboxNotFoundError(msg)


def _stable_uid(workspace_id: str) -> int:
    """Derive a stable, non-root uid in [10000, 60000) from a workspace id.

    Using a per-tenant uid prevents one workspace's processes from signaling
    another's by uid even in the unlikely event of a sandbox escape that lands
    on the host kernel.
    """
    digest = sum(ord(c) for c in workspace_id) if workspace_id else 0
    return 10000 + (digest % 50000)
