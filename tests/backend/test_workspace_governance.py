"""Tests for workspace quota and rate-limiting helpers."""

from __future__ import annotations
import pytest
import redis
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)
from orcheo_backend.app.workspace_governance import WorkspaceGovernance


def test_workspace_rate_limit_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace rate limiting should work when Redis is unavailable."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )
    limiter = WorkspaceGovernance(
        api_rate_limit=1,
        api_rate_interval_seconds=60,
        redis_url="redis://broken",
    )
    limiter.check_api_rate_limit("workspace-a")
    with pytest.raises(WorkspaceRateLimitError):
        limiter.check_api_rate_limit("workspace-a")


def test_workspace_run_slot_reservation_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent run slots should reserve and release cleanly in memory."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )
    limiter = WorkspaceGovernance(
        api_rate_limit=10,
        api_rate_interval_seconds=60,
        redis_url="redis://broken",
    )
    limiter.reserve_run_slot("workspace-a", limit=1)
    with pytest.raises(WorkspaceQuotaExceededError):
        limiter.reserve_run_slot("workspace-a", limit=1)
    limiter.release_run_slot("workspace-a")
    limiter.reserve_run_slot("workspace-a", limit=1)
