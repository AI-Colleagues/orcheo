"""Workspace quota, rate-limit, and concurrency helpers."""

from __future__ import annotations
import secrets
from collections import deque
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, cast
import redis
from orcheo.config import get_settings
from orcheo.workspace import WorkspaceQuotas
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)


_TENANT_GOVERNANCE_CACHE: dict[str, WorkspaceGovernance | None] = {"manager": None}


def _coerce_int(value: Any, default: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return candidate


def _workspace_quota_value(quotas: WorkspaceQuotas, name: str, default: int) -> int:
    value = getattr(quotas, name, default)
    return _coerce_int(value, default)


def _workspace_context_id(workspace: Any) -> str:
    """Return a stable workspace identifier from either workspace or request context."""
    workspace_id = getattr(workspace, "workspace_id", None)
    if workspace_id is None:
        workspace_id = getattr(workspace, "id", None)
    if workspace_id is None:
        msg = "Workspace context is missing an identifier."
        raise AttributeError(msg)
    return str(workspace_id)


def _workspace_context_slug(workspace: Any) -> str:
    """Return a human-readable workspace slug from workspace records or context."""
    slug = getattr(workspace, "slug", None)
    if slug is None:
        slug = getattr(workspace, "workspace_slug", None)
    if slug is None:
        return "workspace"
    return str(slug)


class WorkspaceGovernance:
    """Best-effort workspace quota and rate-limit enforcement."""

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

    def check_api_rate_limit(self, workspace_id: str) -> None:
        """Enforce the per-workspace API rate limit."""
        if self._api_rate_limit == 0 or not workspace_id:
            return

        now = datetime.now(tz=UTC)
        window_start = now - timedelta(seconds=self._api_rate_interval_seconds)
        if self._redis is not None:
            try:
                key = self._api_key(workspace_id)
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start.timestamp())
                member = f"{now.timestamp()}:{secrets.token_hex(8)}"
                pipe.zadd(key, {member: now.timestamp()})
                pipe.zcard(key)
                pipe.expire(key, self._api_rate_interval_seconds)
                _, _, count, _ = pipe.execute()
                if int(count) > self._api_rate_limit:
                    self._redis.zrem(key, member)
                    raise WorkspaceRateLimitError(
                        f"Too many requests for workspace {workspace_id}",
                        code="workspace.rate_limited",
                        retry_after=self._api_rate_interval_seconds,
                    )
                return
            except redis.RedisError:
                pass

        with self._lock:
            bucket = self._api_events.setdefault(workspace_id, deque())
            while bucket and bucket[0] <= window_start:
                bucket.popleft()
            if len(bucket) >= self._api_rate_limit:
                raise WorkspaceRateLimitError(
                    f"Too many requests for workspace {workspace_id}",
                    code="workspace.rate_limited",
                    retry_after=self._api_rate_interval_seconds,
                )
            bucket.append(now)

    def reserve_run_slot(self, workspace_id: str, *, limit: int) -> None:
        """Reserve one concurrent run slot for a workspace."""
        if limit <= 0 or not workspace_id:
            return

        if self._redis is not None:
            try:
                key = self._run_key(workspace_id)
                current = cast(int, self._redis.incr(key))
                if current == 1:
                    self._redis.expire(key, 24 * 60 * 60)
                if current > limit:
                    self._redis.decr(key)
                    raise WorkspaceQuotaExceededError(
                        f"Workspace {workspace_id} reached its concurrent run limit",
                        code="workspace.quota.concurrent_runs",
                        details={"limit": limit, "current": current - 1},
                    )
                return
            except redis.RedisError:
                pass

        with self._lock:
            current = self._run_counts.get(workspace_id, 0) + 1
            if current > limit:
                raise WorkspaceQuotaExceededError(
                    f"Workspace {workspace_id} reached its concurrent run limit",
                    code="workspace.quota.concurrent_runs",
                    details={"limit": limit, "current": current - 1},
                )
            self._run_counts[workspace_id] = current

    def release_run_slot(self, workspace_id: str) -> None:
        """Release one concurrent run slot for a workspace."""
        if not workspace_id:
            return

        if self._redis is not None:
            try:
                key = self._run_key(workspace_id)
                current = cast(int, self._redis.decr(key))
                if current <= 0:
                    self._redis.delete(key)
                return
            except redis.RedisError:
                pass

        with self._lock:
            current = self._run_counts.get(workspace_id, 0) - 1
            if current <= 0:
                self._run_counts.pop(workspace_id, None)
            else:
                self._run_counts[workspace_id] = current

    def _api_key(self, workspace_id: str) -> str:
        return f"orcheo:workspace:rate:{workspace_id}"

    def _run_key(self, workspace_id: str) -> str:
        return f"orcheo:workspace:runs:{workspace_id}"


def get_workspace_governance(*, refresh: bool = False) -> WorkspaceGovernance:
    """Return the cached workspace governance manager."""
    if refresh:
        _TENANT_GOVERNANCE_CACHE["manager"] = None
    manager = _TENANT_GOVERNANCE_CACHE.get("manager")
    if manager is None:
        settings = get_settings()
        api_rate_limit = _coerce_int(
            settings.get("MULTI_WORKSPACE_RATE_LIMIT", 120),
            120,
        )
        api_rate_interval_seconds = _coerce_int(
            settings.get("MULTI_WORKSPACE_RATE_LIMIT_INTERVAL", 60),
            60,
        )
        redis_url = settings.get("REDIS_URL", "redis://localhost:6379/0")
        manager = WorkspaceGovernance(
            api_rate_limit=api_rate_limit,
            api_rate_interval_seconds=api_rate_interval_seconds,
            redis_url=str(redis_url) if redis_url else None,
        )
        _TENANT_GOVERNANCE_CACHE["manager"] = manager
    return manager


async def ensure_workspace_workflow_quota(
    repository: Any,
    workspace: Any,
) -> None:
    """Validate workflow, storage, and credential-related workspace quotas."""
    workspace_id = _workspace_context_id(workspace)
    workspace_slug = _workspace_context_slug(workspace)
    workflows = await repository.list_workflows(
        include_archived=True,
        workspace_id=workspace_id,
    )
    storage_rows = len(workflows)

    versions_count = 0
    runs_count = 0
    for workflow in workflows:
        versions = await repository.list_versions(workflow.id)
        versions_count += len(versions)
        runs = await repository.list_runs_for_workflow(
            workflow.id, workspace_id=workspace_id
        )
        runs_count += len(runs)
    storage_rows += versions_count + runs_count

    if len(workflows) >= workspace.quotas.max_workflows:
        raise WorkspaceQuotaExceededError(
            f"Workspace {workspace_slug} reached its workflow quota",
            code="workspace.quota.workflows",
            details={
                "limit": workspace.quotas.max_workflows,
                "current": len(workflows),
            },
        )
    if storage_rows >= workspace.quotas.max_storage_rows:
        raise WorkspaceQuotaExceededError(
            f"Workspace {workspace_slug} reached its storage quota",
            code="workspace.quota.storage",
            details={
                "limit": workspace.quotas.max_storage_rows,
                "current": storage_rows,
            },
        )


async def ensure_workspace_credential_quota(
    vault: Any,
    workspace: Any,
) -> None:
    """Validate the credential quota for a workspace."""
    workspace_id = _workspace_context_id(workspace)
    workspace_slug = _workspace_context_slug(workspace)
    credentials = vault.list_all_credentials(workspace_id=workspace_id)
    if len(credentials) >= workspace.quotas.max_credentials:
        raise WorkspaceQuotaExceededError(
            f"Workspace {workspace_slug} reached its credential quota",
            code="workspace.quota.credentials",
            details={
                "limit": workspace.quotas.max_credentials,
                "current": len(credentials),
            },
        )


def resolve_workspace_quota(
    quotas: WorkspaceQuotas,
    name: str,
    default: int,
) -> int:
    """Return an integer quota value from a workspace configuration."""
    return _workspace_quota_value(quotas, name, default)
