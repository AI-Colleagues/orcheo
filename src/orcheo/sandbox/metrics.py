"""Per-sandbox resource metrics and warm-pool autoscaling.

Two surfaces:

- ``MetricsRecorder``: an in-process counter store the manager calls on
  lifecycle events. The default ``InMemoryMetricsRecorder`` is good enough
  for tests and a single-host dev install; production deployments swap in a
  Prometheus / OpenTelemetry-backed implementation.

- ``WarmPoolAutoscaler``: a pure function over ``WorkspaceRuntimePool`` and
  recent usage that recommends a target pool size. The reaper drives the
  pool *down*; the autoscaler is responsible for keeping it *up* to
  ``pool_min`` and growing toward usage spikes.
"""

from __future__ import annotations
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from orcheo.sandbox.models import WorkspaceRuntimePool


class MetricsRecorder(Protocol):
    """Minimal recorder surface used by the Sandbox Runtime Manager."""

    def record_provision(self, workspace_id: str) -> None:
        """Increment the provision counter."""

    def record_destroy(self, workspace_id: str) -> None:
        """Increment the destroy counter."""

    def record_egress_denied(self, workspace_id: str) -> None:
        """Increment the egress-denied counter."""

    def snapshot(self) -> Mapping[str, int]:
        """Return a flat snapshot of counter values keyed by metric name."""


@dataclass
class InMemoryMetricsRecorder:
    """Thread-safe in-memory counter store."""

    _counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _bump(self, key: str, by: int = 1) -> None:
        with self._lock:
            self._counters[key] += by

    def record_provision(self, workspace_id: str) -> None:
        """Increment the provision counter for ``workspace``."""
        self._bump(f"sandbox.provision.{workspace_id}")

    def record_destroy(self, workspace_id: str) -> None:
        """Increment the destroy counter for ``workspace``."""
        self._bump(f"sandbox.destroy.{workspace_id}")

    def record_egress_denied(self, workspace_id: str) -> None:
        """Increment the egress-denied counter for ``workspace``."""
        self._bump(f"sandbox.egress_denied.{workspace_id}")

    def snapshot(self) -> Mapping[str, int]:
        """Return a copy of all counter values."""
        with self._lock:
            return dict(self._counters)


def recommend_pool_size(
    pool: WorkspaceRuntimePool,
    *,
    current_pool_size: int,
    recent_concurrent_max: int,
) -> int:
    """Compute the target warm-pool size for a workspace.

    Args:
        pool: Pool config for the workspace.
        current_pool_size: Number of warm-ready sandboxes right now.
        recent_concurrent_max: Peak concurrent in-use sandboxes observed in the
            most recent window. Drives upward scaling toward the next run-spike.

    Returns:
        Recommended pool size, bounded by ``[pool_min, pool_max]``.
    """
    target = max(pool.pool_min, recent_concurrent_max)
    target = min(target, pool.pool_max)
    # Bias toward stability — keep at least the current size unless we are
    # over max (the reaper handles shrink-on-idle).
    if current_pool_size > target and current_pool_size <= pool.pool_max:
        return current_pool_size
    return target


class WarmPoolAutoscaler:
    """Decide when to provision additional warm sandboxes per workspace."""

    def __init__(self, recorder: MetricsRecorder | None = None) -> None:
        """Initialize the autoscaler.

        Args:
            recorder: Optional metrics sink for ``decision`` events.
        """
        self._recorder = recorder

    def decide(
        self,
        pool: WorkspaceRuntimePool,
        *,
        current_pool_size: int,
        recent_concurrent_max: int,
    ) -> int:
        """Return how many additional warm sandboxes to provision (>= 0)."""
        target = recommend_pool_size(
            pool,
            current_pool_size=current_pool_size,
            recent_concurrent_max=recent_concurrent_max,
        )
        delta = max(0, target - current_pool_size)
        if delta and self._recorder is not None:
            self._recorder.record_provision(pool.workspace_id)
        return delta
