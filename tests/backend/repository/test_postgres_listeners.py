"""Tests for PostgreSQL listener repository methods.

Covers all uncovered lines in repository_postgres/_listeners.py and the
_dump_listener_cursor / _dump_listener_dedupe static methods in _base.py.
"""

from __future__ import annotations
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
import pytest
from orcheo.listeners import (
    ListenerCursor,
    ListenerSubscriptionStatus,
)
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_postgres import _base as pg_base


# ---------------------------------------------------------------------------
# Fake DB infrastructure (mirrors test_postgres_integration.py)
# ---------------------------------------------------------------------------


class FakeRow(dict[str, Any]):
    """Fake row supporting both key and integer access."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeCursor:
    """Fake cursor returning pre-configured rows."""

    def __init__(
        self, *, row: dict[str, Any] | None = None, rows: list[Any] | None = None
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]

    async def fetchone(self) -> FakeRow | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake connection recording queries and serving configured responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(row=response.get("row"), rows=response.get("rows"))
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakePool:
    """Fake pool returning a single shared connection."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def connection(self) -> FakeConnection:
        return self._connection


def make_repo(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresWorkflowRepository:
    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())
    repo = PostgresWorkflowRepository("postgresql://test")
    repo._pool = FakePool(FakeConnection(responses))  # noqa: SLF001
    repo._initialized = initialized  # noqa: SLF001
    return repo


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _subscription_payload(
    subscription_id: UUID,
    workflow_id: UUID,
    version_id: UUID,
    *,
    status: str = "active",
    assigned_runtime: str | None = None,
    lease_expires_at: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "id": str(subscription_id),
        "workflow_id": str(workflow_id),
        "workflow_version_id": str(version_id),
        "node_name": "telegram_listener",
        "platform": "telegram",
        "bot_identity_key": "telegram:test-token",
        "config": {},
        "status": status,
        "assigned_runtime": assigned_runtime,
        "lease_expires_at": lease_expires_at,
        "last_event_at": None,
        "last_error": last_error,
        "audit_log": [],
        "created_at": now,
        "updated_at": now,
    }


def _version_payload(version_id: UUID, workflow_id: UUID) -> dict[str, Any]:
    now = _now_iso()
    return {
        "id": str(version_id),
        "workflow_id": str(workflow_id),
        "version": 1,
        "graph": {},
        "metadata": {},
        "runnable_config": None,
        "notes": None,
        "created_by": "author",
        "audit_log": [],
        "created_at": now,
        "updated_at": now,
    }


def _cursor_payload(subscription_id: UUID) -> dict[str, Any]:
    return {
        "subscription_id": str(subscription_id),
        "telegram_offset": 99,
        "updated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# _base.py: _dump_listener_cursor / _dump_listener_dedupe (lines 201, 205)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dump_listener_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dump_listener_cursor serialises a ListenerCursor to JSON."""
    repo = make_repo(monkeypatch, [])
    subscription_id = uuid4()
    cursor = ListenerCursor(subscription_id=subscription_id, telegram_offset=7)
    dumped = repo._dump_listener_cursor(cursor)  # noqa: SLF001
    parsed = json.loads(dumped)
    assert parsed["subscription_id"] == str(subscription_id)
    assert parsed["telegram_offset"] == 7


@pytest.mark.asyncio
async def test_dump_listener_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dump_listener_dedupe serialises a ListenerDedupeRecord to JSON."""
    from orcheo.listeners import ListenerDedupeRecord

    repo = make_repo(monkeypatch, [])
    subscription_id = uuid4()
    record = ListenerDedupeRecord(subscription_id=subscription_id, dedupe_key="tg:42")
    dumped = repo._dump_listener_dedupe(record)  # noqa: SLF001
    parsed = json.loads(dumped)
    assert parsed["subscription_id"] == str(subscription_id)
    assert parsed["dedupe_key"] == "tg:42"


# ---------------------------------------------------------------------------
# list_listener_subscriptions (lines 146-166)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_listener_subscriptions_no_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns all subscriptions when no workflow_id filter is given."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(monkeypatch, [{"rows": [{"payload": payload}]}])

    result = await repo.list_listener_subscriptions()

    assert len(result) == 1
    assert result[0].id == sub_id


@pytest.mark.asyncio
async def test_list_listener_subscriptions_with_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passes workflow_id filter in the SQL query."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(monkeypatch, [{"rows": [{"payload": payload}]}])

    result = await repo.list_listener_subscriptions(workflow_id=wf_id)

    assert len(result) == 1
    assert result[0].workflow_id == wf_id


# ---------------------------------------------------------------------------
# get_listener_subscription (lines 172-193)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_listener_subscription_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    result = await repo.get_listener_subscription(sub_id)

    assert result.id == sub_id
    assert result.status == ListenerSubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_get_listener_subscription_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{}])  # fetchone returns None

    with pytest.raises(WorkflowNotFoundError):
        await repo.get_listener_subscription(uuid4())


# ---------------------------------------------------------------------------
# claim_listener_subscription (lines 195-267)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_subscription_row_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 216: returns None when the subscription row is absent."""
    repo = make_repo(monkeypatch, [{}])  # fetchone → None

    result = await repo.claim_listener_subscription(
        uuid4(), runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


@pytest.mark.asyncio
async def test_claim_subscription_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 225: returns None when status is not ACTIVE."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id, status="disabled")
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    result = await repo.claim_listener_subscription(
        sub_id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


@pytest.mark.asyncio
async def test_claim_subscription_held_by_other_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 232: returns None when another runtime holds a valid lease."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    future = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    payload = _subscription_payload(
        sub_id,
        wf_id,
        ver_id,
        assigned_runtime="other-runtime",
        lease_expires_at=future,
    )
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    result = await repo.claim_listener_subscription(
        sub_id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


@pytest.mark.asyncio
async def test_claim_subscription_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful claim updates assignment and returns the subscription."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    # SELECT → subscription row; UPDATE RETURNING → row confirming update
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},
            {"row": {"id": str(sub_id)}},
        ],
    )

    result = await repo.claim_listener_subscription(
        sub_id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is not None
    assert result.assigned_runtime == "rt-1"
    assert result.lease_expires_at is not None


@pytest.mark.asyncio
async def test_claim_subscription_same_runtime_skips_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-claiming by the same runtime skips record_event (branch 236->241 False)."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    # already assigned to "rt-1" so should_record_claim == False
    payload = _subscription_payload(sub_id, wf_id, ver_id, assigned_runtime="rt-1")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},
            {"row": {"id": str(sub_id)}},
        ],
    )

    result = await repo.claim_listener_subscription(
        sub_id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is not None
    assert result.assigned_runtime == "rt-1"
    # audit_log should still be empty because same runtime did not record a new event
    assert result.audit_log == []


@pytest.mark.asyncio
async def test_claim_subscription_conflict_on_db_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UPDATE RETURNING returns nothing when a concurrent claim wins the race."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    # SELECT → row; UPDATE RETURNING → no row (race lost)
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},
            {},
        ],
    )

    result = await repo.claim_listener_subscription(
        sub_id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


# ---------------------------------------------------------------------------
# release_listener_subscription (lines 269-312)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_subscription_row_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{}])

    result = await repo.release_listener_subscription(uuid4(), runtime_id="rt-1")
    assert result is None


@pytest.mark.asyncio
async def test_release_subscription_wrong_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id, assigned_runtime="other")
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    result = await repo.release_listener_subscription(sub_id, runtime_id="rt-1")
    assert result is None


@pytest.mark.asyncio
async def test_release_subscription_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id, assigned_runtime="rt-1")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},
            {},  # UPDATE
        ],
    )

    result = await repo.release_listener_subscription(sub_id, runtime_id="rt-1")
    assert result is not None
    assert result.assigned_runtime is None
    assert result.lease_expires_at is None


# ---------------------------------------------------------------------------
# get_listener_cursor (lines 314-334)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_listener_cursor_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{}])

    result = await repo.get_listener_cursor(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_listener_cursor_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    payload = _cursor_payload(sub_id)
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    result = await repo.get_listener_cursor(sub_id)
    assert result is not None
    assert result.subscription_id == sub_id
    assert result.telegram_offset == 99


# ---------------------------------------------------------------------------
# save_listener_cursor (lines 336-359)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_listener_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    cursor = ListenerCursor(subscription_id=sub_id, telegram_offset=42)
    repo = make_repo(monkeypatch, [{}])  # INSERT/UPSERT

    saved = await repo.save_listener_cursor(cursor)

    assert saved.subscription_id == sub_id
    assert saved.telegram_offset == 42


# ---------------------------------------------------------------------------
# dispatch_listener_event (lines 361-454)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_listener_event_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{}])  # SELECT → None

    from orcheo.listeners import ListenerDispatchPayload

    with pytest.raises(WorkflowNotFoundError):
        await repo.dispatch_listener_event(
            uuid4(),
            ListenerDispatchPayload(
                platform="telegram",
                event_type="message",
                dedupe_key="tg:1",
                bot_identity="telegram:tok",
            ),
        )


@pytest.mark.asyncio
async def test_dispatch_listener_event_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id, status="paused")
    repo = make_repo(monkeypatch, [{"row": {"payload": payload}}])

    from orcheo.listeners import ListenerDispatchPayload

    result = await repo.dispatch_listener_event(
        sub_id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="tg:1",
            bot_identity="telegram:tok",
        ),
    )
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_listener_event_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None when the dedupe check finds an existing key."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},  # SELECT subscription
            {},  # DELETE expired dedupe
            {"row": {"1": 1}},  # SELECT dedupe → duplicate found
        ],
    )

    from orcheo.listeners import ListenerDispatchPayload

    result = await repo.dispatch_listener_event(
        sub_id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="tg:1",
            bot_identity="telegram:tok",
        ),
    )
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_listener_event_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full success path: creates a workflow run from the listener event."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    sub_payload = _subscription_payload(sub_id, wf_id, ver_id)
    ver_payload = _version_payload(ver_id, wf_id)

    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": sub_payload}},  # SELECT subscription
            {},  # DELETE expired dedupe
            {},  # SELECT dedupe → not a dup (fetchone = None)
            {},  # INSERT listener_dedupe
            {},  # UPDATE listener_subscriptions
            # _get_version_locked (explicit call in dispatch_listener_event)
            {"row": {"payload": ver_payload}},
            # _create_run_locked → _get_version_locked (internal)
            {"row": {"payload": ver_payload}},
            {},  # INSERT workflow_runs
        ],
    )

    from orcheo.listeners import ListenerDispatchMessage, ListenerDispatchPayload

    run = await repo.dispatch_listener_event(
        sub_id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="tg:99",
            bot_identity="telegram:tok",
            message=ListenerDispatchMessage(chat_id="123", text="hi"),
            reply_target={"chat_id": "123"},
        ),
    )

    assert run is not None
    assert run.triggered_by == "listener"
    assert run.workflow_version_id == ver_id


# ---------------------------------------------------------------------------
# update_listener_subscription_status (lines 456-508)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_subscription_status_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{}])

    with pytest.raises(WorkflowNotFoundError):
        await repo.update_listener_subscription_status(
            uuid4(), status=ListenerSubscriptionStatus.ACTIVE, actor="test"
        )


@pytest.mark.asyncio
async def test_update_subscription_status_active_clears_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting status to ACTIVE clears last_error."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(
        sub_id, wf_id, ver_id, last_error="connection refused"
    )
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},  # SELECT
            {},  # UPDATE
        ],
    )

    result = await repo.update_listener_subscription_status(
        sub_id, status=ListenerSubscriptionStatus.ACTIVE, actor="supervisor"
    )

    assert result.status == ListenerSubscriptionStatus.ACTIVE
    assert result.last_error is None


@pytest.mark.asyncio
async def test_update_subscription_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting status to ERROR preserves last_error field."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},  # SELECT
            {},  # UPDATE
        ],
    )

    result = await repo.update_listener_subscription_status(
        sub_id, status=ListenerSubscriptionStatus.ERROR, actor="supervisor"
    )

    assert result.status == ListenerSubscriptionStatus.ERROR


@pytest.mark.asyncio
async def test_update_subscription_status_blocked_sets_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting status to BLOCKED records the blocking reason."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": payload}},  # SELECT
            {},  # UPDATE
        ],
    )

    result = await repo.update_listener_subscription_status(
        sub_id,
        status=ListenerSubscriptionStatus.BLOCKED,
        actor="supervisor",
        last_error="Credential 'tg_token' was not found in the configured vault",
    )

    assert result.status == ListenerSubscriptionStatus.BLOCKED
    assert "tg_token" in (result.last_error or "")


# ---------------------------------------------------------------------------
# sync_listener_subscriptions_for_version (lines 510-527)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_listener_subscriptions_empty_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty graph: disables existing subs (none found) and inserts nothing."""
    repo = make_repo(
        monkeypatch,
        [{"rows": []}],  # SELECT for _disable: no active subscriptions
    )

    await repo.sync_listener_subscriptions_for_version(
        uuid4(), uuid4(), {}, actor="author"
    )


@pytest.mark.asyncio
async def test_sync_listener_subscriptions_with_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph with a listener: compiles and inserts one subscription."""
    wf_id = uuid4()
    ver_id = uuid4()
    graph: dict[str, Any] = {
        "nodes": [],
        "edges": [],
        "index": {
            "listeners": [
                {
                    "node_name": "tg_bot",
                    "platform": "telegram",
                    "token": "[[tg_token]]",
                }
            ]
        },
    }
    repo = make_repo(
        monkeypatch,
        [
            {"rows": []},  # SELECT for _disable: no active subscriptions
            {},  # INSERT new subscription
        ],
    )

    await repo.sync_listener_subscriptions_for_version(
        wf_id, ver_id, graph, actor="author"
    )


# ---------------------------------------------------------------------------
# _disable_listener_subscriptions_locked with conn=None (lines 81-82)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_listener_subscriptions_no_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines 81-82: opens its own connection when conn is None."""
    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    payload = _subscription_payload(sub_id, wf_id, ver_id)
    repo = make_repo(
        monkeypatch,
        [
            {"rows": [{"id": str(sub_id), "payload": payload}]},  # SELECT
            {},  # UPDATE
        ],
    )

    await repo._disable_listener_subscriptions_locked(  # noqa: SLF001
        wf_id, actor="test"
    )


# ---------------------------------------------------------------------------
# _replace_listener_subscriptions_locked: inserts new subscriptions (lines 98-139)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_listener_subscriptions_inserts_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing active subscriptions are disabled before new ones are inserted."""
    from orcheo.listeners import ListenerPlatform, ListenerSubscription

    sub_id = uuid4()
    wf_id = uuid4()
    ver_id = uuid4()
    existing_payload = _subscription_payload(sub_id, wf_id, ver_id)

    # New subscription to insert
    new_sub = ListenerSubscription(
        workflow_id=wf_id,
        workflow_version_id=ver_id,
        node_name="tg_listener",
        platform=ListenerPlatform.TELEGRAM,
        bot_identity_key="telegram:new-token",
    )

    repo = make_repo(
        monkeypatch,
        [
            # _disable: SELECT active subs
            {"rows": [{"id": str(sub_id), "payload": existing_payload}]},
            # _disable: UPDATE the existing sub to DISABLED
            {},
            # INSERT new subscription
            {},
        ],
    )

    await repo._replace_listener_subscriptions_locked(  # noqa: SLF001
        wf_id, [new_sub], actor="author"
    )
