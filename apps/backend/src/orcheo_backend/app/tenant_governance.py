"""Tenant quota, rate-limit, and concurrency helpers."""

from __future__ import annotations
import secrets
from collections import deque
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
import redis
from orcheo.config import get_settings
from orcheo.tenancy import Tenant, TenantQuotas
from orcheo_backend.app.errors import (
    TenantQuotaExceededError,
    TenantRateLimitError,
)


_TENANT_GOVERNANCE_CACHE: dict[str, TenantGovernance | None] = {"manager": None}


def _coerce_int(value: Any, default: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return candidate


def _tenant_quota_value(quotas: TenantQuotas, name: str, default: int) -> int:
    value = getattr(quotas, name, default)
    return _coerce_int(value, default)


class TenantGovernance:
    """Best-effort tenant quota and rate-limit enforcement."""

    def __init__(
        self,
        *,
        api_rate_limit: int,
        api_rate_interval_seconds: int,
        redis_url: str | None = None,
    ) -> None:
        """Initialize governance with rate-limit config and optional Redis backend."""
        self._api_rate_limit = max(api_rate_limit, 0)
        self._api_rate_interval_seconds = max(api_rate_interval_seconds, 1)
        self._lock = RLock()
        self._api_events: dict[str, deque[datetime]] = {}
        self._run_counts: dict[str, int] = {}
        self._redis: redis.Redis | None = None
        resolved_redis_url = redis_url or str(
            get_settings().get("REDIS_URL", "redis://localhost:6379/0")
        )
        try:
            self._redis = redis.from_url(resolved_redis_url, decode_responses=True)
        except redis.RedisError:
            self._redis = None

    def check_api_rate_limit(self, tenant_id: str) -> None:
        """Enforce the per-tenant API rate limit."""
        if self._api_rate_limit == 0 or not tenant_id:
            return

        now = datetime.now(tz=UTC)
        window_start = now - timedelta(seconds=self._api_rate_interval_seconds)
        if self._redis is not None:
            try:
                key = self._api_key(tenant_id)
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start.timestamp())
                member = f"{now.timestamp()}:{secrets.token_hex(8)}"
                pipe.zadd(key, {member: now.timestamp()})
                pipe.zcard(key)
                pipe.expire(key, self._api_rate_interval_seconds)
                _, _, count, _ = pipe.execute()
                if int(count) > self._api_rate_limit:
                    self._redis.zrem(key, member)
                    raise TenantRateLimitError(
                        f"Too many requests for tenant {tenant_id}",
                        code="tenant.rate_limited",
                        retry_after=self._api_rate_interval_seconds,
                    )
                return
            except redis.RedisError:
                pass

        with self._lock:
            bucket = self._api_events.setdefault(tenant_id, deque())
            while bucket and bucket[0] <= window_start:
                bucket.popleft()
            if len(bucket) >= self._api_rate_limit:
                raise TenantRateLimitError(
                    f"Too many requests for tenant {tenant_id}",
                    code="tenant.rate_limited",
                    retry_after=self._api_rate_interval_seconds,
                )
            bucket.append(now)

    def reserve_run_slot(self, tenant_id: str, *, limit: int) -> None:
        """Reserve one concurrent run slot for a tenant."""
        if limit <= 0 or not tenant_id:
            return

        if self._redis is not None:
            try:
                key = self._run_key(tenant_id)
                current = int(self._redis.incr(key))
                if current == 1:
                    self._redis.expire(key, 24 * 60 * 60)
                if current > limit:
                    self._redis.decr(key)
                    raise TenantQuotaExceededError(
                        f"Tenant {tenant_id} reached its concurrent run limit",
                        code="tenant.quota.concurrent_runs",
                        details={"limit": limit, "current": current - 1},
                    )
                return
            except redis.RedisError:
                pass

        with self._lock:
            current = self._run_counts.get(tenant_id, 0) + 1
            if current > limit:
                raise TenantQuotaExceededError(
                    f"Tenant {tenant_id} reached its concurrent run limit",
                    code="tenant.quota.concurrent_runs",
                    details={"limit": limit, "current": current - 1},
                )
            self._run_counts[tenant_id] = current

    def release_run_slot(self, tenant_id: str) -> None:
        """Release one concurrent run slot for a tenant."""
        if not tenant_id:
            return

        if self._redis is not None:
            try:
                key = self._run_key(tenant_id)
                current = int(self._redis.decr(key))
                if current <= 0:
                    self._redis.delete(key)
                return
            except redis.RedisError:
                pass

        with self._lock:
            current = self._run_counts.get(tenant_id, 0) - 1
            if current <= 0:
                self._run_counts.pop(tenant_id, None)
            else:
                self._run_counts[tenant_id] = current

    def _api_key(self, tenant_id: str) -> str:
        return f"orcheo:tenant:rate:{tenant_id}"

    def _run_key(self, tenant_id: str) -> str:
        return f"orcheo:tenant:runs:{tenant_id}"


def get_tenant_governance(*, refresh: bool = False) -> TenantGovernance:
    """Return the cached tenant governance manager."""
    if refresh:
        _TENANT_GOVERNANCE_CACHE["manager"] = None
    manager = _TENANT_GOVERNANCE_CACHE.get("manager")
    if manager is None:
        settings = get_settings()
        api_rate_limit = _coerce_int(
            settings.get("MULTI_TENANCY_RATE_LIMIT", 120),
            120,
        )
        api_rate_interval_seconds = _coerce_int(
            settings.get("MULTI_TENANCY_RATE_LIMIT_INTERVAL", 60),
            60,
        )
        redis_url = settings.get("REDIS_URL", "redis://localhost:6379/0")
        manager = TenantGovernance(
            api_rate_limit=api_rate_limit,
            api_rate_interval_seconds=api_rate_interval_seconds,
            redis_url=str(redis_url) if redis_url else None,
        )
        _TENANT_GOVERNANCE_CACHE["manager"] = manager
    return manager


async def ensure_tenant_workflow_quota(
    repository: Any,
    tenant: Tenant,
) -> None:
    """Validate workflow, storage, and credential-related tenant quotas."""
    tenant_id = str(tenant.id)
    workflows = await repository.list_workflows(
        include_archived=True,
        tenant_id=tenant_id,
    )
    storage_rows = len(workflows)

    versions_count = 0
    runs_count = 0
    for workflow in workflows:
        versions = await repository.list_versions(workflow.id)
        versions_count += len(versions)
        runs = await repository.list_runs_for_workflow(workflow.id, tenant_id=tenant_id)
        runs_count += len(runs)
    storage_rows += versions_count + runs_count

    if len(workflows) >= tenant.quotas.max_workflows:
        raise TenantQuotaExceededError(
            f"Tenant {tenant.slug} reached its workflow quota",
            code="tenant.quota.workflows",
            details={
                "limit": tenant.quotas.max_workflows,
                "current": len(workflows),
            },
        )
    if storage_rows >= tenant.quotas.max_storage_rows:
        raise TenantQuotaExceededError(
            f"Tenant {tenant.slug} reached its storage quota",
            code="tenant.quota.storage",
            details={
                "limit": tenant.quotas.max_storage_rows,
                "current": storage_rows,
            },
        )


async def ensure_tenant_credential_quota(
    vault: Any,
    tenant: Tenant,
) -> None:
    """Validate the credential quota for a tenant."""
    tenant_id = str(tenant.id)
    credentials = vault.list_all_credentials(tenant_id=tenant_id)
    if len(credentials) >= tenant.quotas.max_credentials:
        raise TenantQuotaExceededError(
            f"Tenant {tenant.slug} reached its credential quota",
            code="tenant.quota.credentials",
            details={
                "limit": tenant.quotas.max_credentials,
                "current": len(credentials),
            },
        )


def resolve_tenant_quota(
    quotas: TenantQuotas,
    name: str,
    default: int,
) -> int:
    """Return an integer quota value from a tenant configuration."""
    return _tenant_quota_value(quotas, name, default)
