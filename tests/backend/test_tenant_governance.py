"""Tests for tenant quota and rate-limiting helpers."""

from __future__ import annotations
import pytest
import redis
from orcheo_backend.app.errors import TenantQuotaExceededError, TenantRateLimitError
from orcheo_backend.app.tenant_governance import TenantGovernance


def test_tenant_rate_limit_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant rate limiting should work when Redis is unavailable."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )
    limiter = TenantGovernance(
        api_rate_limit=1,
        api_rate_interval_seconds=60,
        redis_url="redis://broken",
    )
    limiter.check_api_rate_limit("tenant-a")
    with pytest.raises(TenantRateLimitError):
        limiter.check_api_rate_limit("tenant-a")


def test_tenant_run_slot_reservation_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent run slots should reserve and release cleanly in memory."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(redis.RedisError("boom")),
    )
    limiter = TenantGovernance(
        api_rate_limit=10,
        api_rate_interval_seconds=60,
        redis_url="redis://broken",
    )
    limiter.reserve_run_slot("tenant-a", limit=1)
    with pytest.raises(TenantQuotaExceededError):
        limiter.reserve_run_slot("tenant-a", limit=1)
    limiter.release_run_slot("tenant-a")
    limiter.reserve_run_slot("tenant-a", limit=1)
