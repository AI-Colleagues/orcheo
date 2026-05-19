"""Tests for sandbox data models."""

from __future__ import annotations
import pytest
from orcheo.sandbox.models import (
    SandboxAuditEvent,
    SandboxLease,
    SandboxState,
    WorkspaceRuntimePool,
)


def test_sandbox_lease_defaults_touch_in_order() -> None:
    """A fresh lease starts in PROVISIONING and touch() advances last_used_at."""
    lease = SandboxLease(
        lease_id="L1",
        workspace_id="W",
        sandbox_id="S1",
    )
    assert lease.state is SandboxState.PROVISIONING
    before = lease.last_used_at
    lease.touch()
    assert lease.last_used_at >= before


def test_workspace_runtime_pool_validates_min_max() -> None:
    """max must be >= min and pid_limit/idle_ttl must be positive."""
    WorkspaceRuntimePool(workspace_id="W", pool_min=1, pool_max=4)
    with pytest.raises(ValueError):
        WorkspaceRuntimePool(workspace_id="W", pool_min=-1)
    with pytest.raises(ValueError):
        WorkspaceRuntimePool(workspace_id="W", pool_min=5, pool_max=2)
    with pytest.raises(ValueError):
        WorkspaceRuntimePool(workspace_id="W", pid_limit=0)
    with pytest.raises(ValueError):
        WorkspaceRuntimePool(workspace_id="W", idle_ttl_seconds=0)


def test_sandbox_audit_event_serializes_for_logging() -> None:
    """Audit events flatten cleanly into structured-log extras."""
    event = SandboxAuditEvent(
        event="provision",
        workspace_id="W",
        sandbox_id="S",
        run_id="R",
        detail="x=1",
    )
    extras = event.as_log_extra()
    assert extras["sandbox_event"] == "provision"
    assert extras["workspace_id"] == "W"
    assert extras["sandbox_id"] == "S"
    assert extras["run_id"] == "R"
    assert extras["detail"] == "x=1"
    assert "created_at" in extras
