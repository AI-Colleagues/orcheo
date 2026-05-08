"""Tests for workspace quota and rate-limiting helpers."""

from __future__ import annotations
from types import SimpleNamespace
import pytest
import redis
from orcheo_backend.app import workspace_governance as governance_mod
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


def test_workspace_governance_helper_branch_coverage() -> None:
    """Helper functions should normalize values and support fallbacks."""

    assert governance_mod._coerce_int("7", 3) == 7
    assert governance_mod._coerce_int("bad", 3) == 3
    assert (
        governance_mod._workspace_quota_value(
            SimpleNamespace(max_workflows="5"), "max_workflows", 1
        )
        == 5
    )
    assert governance_mod._workspace_context_id(SimpleNamespace(id="workspace-1")) == (
        "workspace-1"
    )
    assert (
        governance_mod._workspace_context_slug(
            SimpleNamespace(workspace_slug="primary")
        )
        == "primary"
    )
    with pytest.raises(AttributeError):
        governance_mod._workspace_context_id(SimpleNamespace())
    assert governance_mod._workspace_context_slug(SimpleNamespace()) == "workspace"


def test_workspace_governance_redis_paths_and_quota_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis-backed branches should execute when a client is available."""

    class FakePipe:
        def __init__(self, count: int) -> None:
            self.count = count

        def zremrangebyscore(self, *args, **kwargs):
            return self

        def zadd(self, *args, **kwargs):
            return self

        def zcard(self, *args, **kwargs):
            return self

        def expire(self, *args, **kwargs):
            return self

        def execute(self):
            return [None, None, self.count, True]

    class FakeRedis:
        def __init__(self) -> None:
            self.rate_pipe = FakePipe(2)
            self.current = 0

        def pipeline(self):
            return self.rate_pipe

        def zrem(self, *args, **kwargs):
            return None

        def incr(self, key):
            self.current += 1
            return self.current

        def expire(self, *args, **kwargs):
            return None

        def decr(self, key):
            self.current -= 1
            return self.current

        def delete(self, key):
            self.current = 0

    fake_redis = FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: fake_redis)

    limiter = WorkspaceGovernance(
        api_rate_limit=1,
        api_rate_interval_seconds=60,
        redis_url="redis://example",
    )

    with pytest.raises(WorkspaceRateLimitError):
        limiter.check_api_rate_limit("workspace-a")

    limiter.reserve_run_slot("workspace-a", limit=1)
    with pytest.raises(WorkspaceQuotaExceededError):
        limiter.reserve_run_slot("workspace-a", limit=1)
    limiter.release_run_slot("workspace-a")
    limiter.release_run_slot("")
    limiter.check_api_rate_limit("")


@pytest.mark.asyncio()
async def test_workspace_quota_helpers_raise_when_limits_are_hit() -> None:
    """Workspace quota helpers should raise structured errors when full."""

    workspace = SimpleNamespace(
        workspace_id="workspace-1",
        slug="primary",
        quotas=SimpleNamespace(
            max_workflows=1,
            max_storage_rows=2,
            max_credentials=1,
        ),
    )

    class Repository:
        async def list_workflows(self, *, include_archived=True, workspace_id=None):
            del include_archived, workspace_id
            return [SimpleNamespace(id="wf-1")]

        async def list_versions(self, workflow_id):
            del workflow_id
            return [object()]

        async def list_runs_for_workflow(self, workflow_id, *, workspace_id=None):
            del workflow_id, workspace_id
            return [object()]

    with pytest.raises(WorkspaceQuotaExceededError) as exc_info:
        await governance_mod.ensure_workspace_workflow_quota(Repository(), workspace)
    assert exc_info.value.code == "workspace.quota.workflows"

    class CredentialVault:
        def list_all_credentials(self, *, workspace_id=None):
            del workspace_id
            return [object()]

    with pytest.raises(WorkspaceQuotaExceededError) as exc_info:
        await governance_mod.ensure_workspace_credential_quota(
            CredentialVault(), workspace
        )
    assert exc_info.value.code == "workspace.quota.credentials"

    assert governance_mod.resolve_workspace_quota(workspace.quotas, "max_workflows", 0)
