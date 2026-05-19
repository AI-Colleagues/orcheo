"""Tests for sandbox metrics and warm-pool autoscaler."""

from __future__ import annotations
from orcheo.sandbox.metrics import (
    InMemoryMetricsRecorder,
    WarmPoolAutoscaler,
    recommend_pool_size,
)
from orcheo.sandbox.models import WorkspaceRuntimePool


def test_recorder_counts_events() -> None:
    """The recorder accumulates counters per metric key."""
    recorder = InMemoryMetricsRecorder()
    recorder.record_provision("ws")
    recorder.record_provision("ws")
    recorder.record_destroy("ws")
    recorder.record_egress_denied("ws")
    snap = recorder.snapshot()
    assert snap["sandbox.provision.ws"] == 2
    assert snap["sandbox.destroy.ws"] == 1
    assert snap["sandbox.egress_denied.ws"] == 1


def test_recommend_pool_size_clamps_to_min_max() -> None:
    """recommend_pool_size obeys pool_min/max."""
    pool = WorkspaceRuntimePool(workspace_id="ws", pool_min=1, pool_max=3)
    assert recommend_pool_size(pool, current_pool_size=0, recent_concurrent_max=0) == 1
    assert recommend_pool_size(pool, current_pool_size=0, recent_concurrent_max=2) == 2
    assert recommend_pool_size(pool, current_pool_size=0, recent_concurrent_max=10) == 3


def test_recommend_pool_size_holds_at_current_until_reaper() -> None:
    """Stable spike behavior — do not shrink between reaper passes."""
    pool = WorkspaceRuntimePool(workspace_id="ws", pool_min=0, pool_max=4)
    # current=3, peak=1 — autoscaler should not shrink to 1 in one pass.
    assert recommend_pool_size(pool, current_pool_size=3, recent_concurrent_max=1) == 3


def test_autoscaler_returns_delta_and_records_provision() -> None:
    """decide() returns 0 when the pool is sufficient and bumps metrics on growth."""
    recorder = InMemoryMetricsRecorder()
    autoscaler = WarmPoolAutoscaler(recorder=recorder)
    pool = WorkspaceRuntimePool(workspace_id="ws", pool_min=2, pool_max=5)
    grow = autoscaler.decide(pool, current_pool_size=0, recent_concurrent_max=0)
    assert grow == 2
    assert recorder.snapshot()["sandbox.provision.ws"] == 1
    no_op = autoscaler.decide(pool, current_pool_size=2, recent_concurrent_max=0)
    assert no_op == 0
