"""Quota lease race and fail-closed semantics."""

from __future__ import annotations

from uuid import uuid4

import pytest

from orcheo.hosted_apps import QuotaExceededError, QuotaLeaseManager


def test_atomic_reserve_commit_release_and_missing_limit_fail_closed() -> None:
    """Reservations cannot race beyond a workspace operation limit."""
    workspace_id = uuid4()
    manager = QuotaLeaseManager({(workspace_id, "anonymous_invocation"): 2})
    first = manager.reserve(workspace_id, "anonymous_invocation", 1)
    manager.commit(first)
    second = manager.reserve(workspace_id, "anonymous_invocation", 1)
    with pytest.raises(QuotaExceededError):
        manager.reserve(workspace_id, "anonymous_invocation", 1)
    manager.release(second)
    manager.reserve(workspace_id, "anonymous_invocation", 1)
    with pytest.raises(QuotaExceededError, match="unavailable"):
        manager.reserve(workspace_id, "new_session", 1)
