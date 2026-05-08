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


def test_check_api_rate_limit_redis_pipeline_error_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 106: redis.RedisError during pipeline.execute() falls back to in-memory rate check."""

    class _PipeError:
        def zremrangebyscore(self, *a, **k):
            return self

        def zadd(self, *a, **k):
            return self

        def zcard(self, *a, **k):
            return self

        def expire(self, *a, **k):
            return self

        def execute(self):
            raise redis.RedisError("pipeline down")

    class _FakeRedis:
        def pipeline(self):
            return _PipeError()

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _FakeRedis())
    limiter = WorkspaceGovernance(
        api_rate_limit=1, api_rate_interval_seconds=60, redis_url="redis://ok"
    )

    limiter.check_api_rate_limit("ws-pipeline-err")
    with pytest.raises(WorkspaceRateLimitError):
        limiter.check_api_rate_limit("ws-pipeline-err")


def test_check_api_rate_limit_evicts_stale_bucket_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 111: bucket.popleft() removes stale entries so a subsequent call succeeds."""
    from datetime import UTC, datetime

    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *a, **k: (_ for _ in ()).throw(redis.RedisError("no redis")),
    )
    limiter = WorkspaceGovernance(
        api_rate_limit=1, api_rate_interval_seconds=10, redis_url="redis://broken"
    )

    limiter.check_api_rate_limit("ws-stale")

    old_time = datetime(2000, 1, 1, tzinfo=UTC)
    with limiter._lock:
        limiter._api_events["ws-stale"].clear()
        limiter._api_events["ws-stale"].append(old_time)

    limiter.check_api_rate_limit("ws-stale")


def test_reserve_run_slot_returns_early_for_zero_or_negative_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 123: early return when limit <= 0 in reserve_run_slot."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *a, **k: (_ for _ in ()).throw(redis.RedisError("no redis")),
    )
    limiter = WorkspaceGovernance(
        api_rate_limit=10, api_rate_interval_seconds=60, redis_url="redis://broken"
    )

    limiter.reserve_run_slot("ws-z", limit=0)
    limiter.reserve_run_slot("ws-z", limit=-1)


def test_reserve_run_slot_redis_error_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 140: redis.RedisError during incr() falls back to in-memory slot tracking."""

    class _FakeRedisIncError:
        def incr(self, key):
            raise redis.RedisError("incr down")

        def expire(self, *a, **k):
            pass

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _FakeRedisIncError())
    limiter = WorkspaceGovernance(
        api_rate_limit=10, api_rate_interval_seconds=60, redis_url="redis://ok"
    )

    limiter.reserve_run_slot("ws-incr-err", limit=5)
    assert limiter._run_counts.get("ws-incr-err") == 1


def test_release_run_slot_redis_skips_delete_when_count_stays_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 161->163: decr returns positive → skip delete and return directly."""

    class _FakeRedis:
        def __init__(self) -> None:
            self.current = 0
            self.deleted = False

        def incr(self, key):
            self.current += 1
            return self.current

        def expire(self, *a, **k):
            pass

        def decr(self, key):
            self.current -= 1
            return self.current

        def delete(self, key):
            self.deleted = True

    fake = _FakeRedis()
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fake)
    limiter = WorkspaceGovernance(
        api_rate_limit=10, api_rate_interval_seconds=60, redis_url="redis://ok"
    )

    limiter.reserve_run_slot("ws-pos", limit=5)
    limiter.reserve_run_slot("ws-pos", limit=5)

    limiter.release_run_slot("ws-pos")
    assert not fake.deleted
    assert fake.current == 1


def test_release_run_slot_redis_error_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines 164-165: redis.RedisError during decr() is caught and in-memory path used."""

    class _FakeRedisDecrError:
        def decr(self, key):
            raise redis.RedisError("decr down")

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _FakeRedisDecrError())
    limiter = WorkspaceGovernance(
        api_rate_limit=10, api_rate_interval_seconds=60, redis_url="redis://ok"
    )
    limiter._run_counts["ws-decr-err"] = 1

    limiter.release_run_slot("ws-decr-err")
    assert "ws-decr-err" not in limiter._run_counts


def test_release_run_slot_memory_keeps_count_when_still_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 172: in-memory else branch - run_counts updated when current > 0."""
    monkeypatch.setattr(
        redis,
        "from_url",
        lambda *a, **k: (_ for _ in ()).throw(redis.RedisError("no redis")),
    )
    limiter = WorkspaceGovernance(
        api_rate_limit=10, api_rate_interval_seconds=60, redis_url="redis://broken"
    )

    limiter.reserve_run_slot("ws-mem", limit=5)
    limiter.reserve_run_slot("ws-mem", limit=5)

    limiter.release_run_slot("ws-mem")
    assert limiter._run_counts.get("ws-mem") == 1


@pytest.mark.asyncio()
async def test_ensure_workspace_workflow_quota_raises_on_storage_rows_exceeded() -> (
    None
):
    """Line 240: raises WorkspaceQuotaExceededError when storage rows exceed quota."""
    workspace = SimpleNamespace(
        workspace_id="workspace-1",
        slug="primary",
        quotas=SimpleNamespace(
            max_workflows=100,
            max_storage_rows=2,
            max_credentials=10,
        ),
    )

    class _Repository:
        async def list_workflows(self, *, include_archived=True, workspace_id=None):
            del include_archived, workspace_id
            return [SimpleNamespace(id="wf-1")]

        async def list_versions(self, workflow_id):
            del workflow_id
            return [object(), object(), object()]

        async def list_runs_for_workflow(self, workflow_id, *, workspace_id=None):
            del workflow_id, workspace_id
            return []

    with pytest.raises(WorkspaceQuotaExceededError) as exc_info:
        await governance_mod.ensure_workspace_workflow_quota(_Repository(), workspace)
    assert exc_info.value.code == "workspace.quota.storage"
