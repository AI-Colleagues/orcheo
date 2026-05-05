"""Tests for the PostgreSQL-backed service token repository.

These tests use in-memory fakes to verify repository behavior without requiring
a real PostgreSQL database connection.
"""

from __future__ import annotations
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
import pytest
from orcheo_backend.app.authentication.service_tokens import (
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_repository import (
    postgres_repository as pg_repo,
)
from orcheo_backend.app.service_token_repository.postgres_repository import (
    PostgresServiceTokenRepository,
)


class FakeRow(dict[str, Any]):
    """Fake row that supports both index and key access like psycopg rows."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]
        self.rowcount = rowcount

    async def fetchone(self) -> FakeRow | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake database connection recording queries and returning configured responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        return None


class FakePool:
    """Fake connection pool that returns a pre-configured connection."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self._opened = False

    async def open(self) -> None:
        self._opened = True

    async def close(self) -> None:
        self._opened = False

    def connection(self) -> FakeConnection:
        return self._connection


def make_repository(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresServiceTokenRepository:
    """Create a PostgresServiceTokenRepository with fake connection pool."""
    monkeypatch.setattr(pg_repo, "_AsyncConnectionPool", object())
    monkeypatch.setattr(pg_repo, "_DictRowFactory", object())
    repo = PostgresServiceTokenRepository("postgresql://test")
    repo._pool = FakePool(FakeConnection(responses))
    repo._initialized = initialized
    return repo


def _token_row(identifier: str, **overrides: Any) -> dict[str, Any]:
    """Generate a fake service token row."""
    now = datetime.now(tz=UTC).isoformat()
    base = {
        "identifier": identifier,
        "secret_hash": "hash-" + identifier,
        "scopes": json.dumps(["read", "write"]),
        "workspace_ids": json.dumps(["ws-1"]),
        "issued_at": now,
        "expires_at": None,
        "rotation_expires_at": None,
        "revoked_at": None,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": None,
        "use_count": 0,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_postgres_service_token_repository_dependency_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that missing psycopg dependency raises RuntimeError."""
    monkeypatch.setattr(pg_repo, "_AsyncConnectionPool", None)
    monkeypatch.setattr(pg_repo, "_DictRowFactory", None)

    with pytest.raises(RuntimeError, match="psycopg"):
        PostgresServiceTokenRepository("postgresql://test")


@pytest.mark.asyncio
async def test_postgres_service_token_repository_get_pool_creates_on_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_pool creates pool on first call."""

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any):
            self.args = args
            self.kwargs = kwargs
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(pg_repo, "_AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_repo, "_DictRowFactory", lambda x: x)
    repo = PostgresServiceTokenRepository(
        "postgresql://test",
        pool_min_size=2,
        pool_max_size=20,
        pool_timeout=10.0,
        pool_max_idle=100.0,
    )

    pool = await repo._get_pool()
    assert pool.opened is True
    assert pool.args[0] == "postgresql://test"

    # Repeated call returns same pool
    pool2 = await repo._get_pool()
    assert pool2 is pool


@pytest.mark.asyncio
async def test_postgres_service_token_repository_ensure_initialized_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_initialized only runs once."""
    responses: list[Any] = []
    # Schema creation DDL statements
    for _ in range(6):  # CREATE TABLE, CREATE INDEX (multiple)
        responses.append({})

    repo = make_repository(monkeypatch, responses, initialized=False)

    # Multiple concurrent calls
    await asyncio.gather(
        repo._ensure_initialized(),
        repo._ensure_initialized(),
        repo._ensure_initialized(),
    )

    assert repo._initialized is True


@pytest.mark.asyncio
async def test_postgres_service_token_repository_list_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that list_all returns all service tokens."""
    responses: list[Any] = [
        {
            "rows": [
                _token_row("token-1"),
                _token_row("token-2"),
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    tokens = await repo.list_all()

    assert len(tokens) == 2
    assert tokens[0].identifier == "token-1"
    assert tokens[1].identifier == "token-2"


@pytest.mark.asyncio
async def test_postgres_service_token_repository_list_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that list_active returns only active tokens."""
    now = datetime.now(tz=UTC)
    future = (now + timedelta(days=1)).isoformat()
    responses: list[Any] = [
        {
            "rows": [
                _token_row("token-1", expires_at=future, revoked_at=None),
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    tokens = await repo.list_active(now=now)

    assert len(tokens) == 1
    assert tokens[0].identifier == "token-1"


@pytest.mark.asyncio
async def test_postgres_service_token_repository_find_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that find_by_id retrieves a token by identifier."""
    responses: list[Any] = [
        {"row": _token_row("token-1")},
    ]
    repo = make_repository(monkeypatch, responses)

    token = await repo.find_by_id("token-1")

    assert token is not None
    assert token.identifier == "token-1"


@pytest.mark.asyncio
async def test_postgres_service_token_repository_find_by_id_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that find_by_id returns None when token not found."""
    responses: list[Any] = [
        {"row": None},
    ]
    repo = make_repository(monkeypatch, responses)

    token = await repo.find_by_id("nonexistent")

    assert token is None


@pytest.mark.asyncio
async def test_postgres_service_token_repository_find_by_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that find_by_hash retrieves a token by secret hash."""
    responses: list[Any] = [
        {"row": _token_row("token-1", secret_hash="specific-hash")},
    ]
    repo = make_repository(monkeypatch, responses)

    token = await repo.find_by_hash("specific-hash")

    assert token is not None
    assert token.secret_hash == "specific-hash"


@pytest.mark.asyncio
async def test_postgres_service_token_repository_find_by_hash_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that find_by_hash returns None when token not found."""
    responses: list[Any] = [
        {"row": None},
    ]
    repo = make_repository(monkeypatch, responses)

    token = await repo.find_by_hash("nonexistent-hash")

    assert token is None


@pytest.mark.asyncio
async def test_postgres_service_token_repository_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that create persists a new service token."""
    now = datetime.now(tz=UTC)
    record = ServiceTokenRecord(
        identifier="token-1",
        secret_hash="hash-1",
        scopes=frozenset({"read", "write"}),
        workspace_ids=frozenset({"ws-1"}),
        issued_at=now,
    )

    responses: list[Any] = [{}]  # INSERT service_tokens
    repo = make_repository(monkeypatch, responses)

    await repo.create(record)
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_service_token_repository_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that update modifies an existing service token."""
    now = datetime.now(tz=UTC)
    record = ServiceTokenRecord(
        identifier="token-1",
        secret_hash="hash-1",
        scopes=frozenset({"read", "write"}),
        workspace_ids=frozenset({"ws-1"}),
        issued_at=now,
    )

    responses: list[Any] = [{}]  # UPDATE service_tokens
    repo = make_repository(monkeypatch, responses)

    await repo.update(record)
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_service_token_repository_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that delete removes a service token."""
    responses: list[Any] = [{}]  # DELETE FROM service_tokens
    repo = make_repository(monkeypatch, responses)

    await repo.delete("token-1")
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_service_token_repository_record_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that record_usage updates last_used_at."""
    responses: list[Any] = [
        {"row": _token_row("token-1")},  # Check if token exists
        {},  # UPDATE last_used_at
    ]
    repo = make_repository(monkeypatch, responses)

    await repo.record_usage("token-1", ip="127.0.0.1", user_agent="test-agent")
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_service_token_repository_record_usage_token_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that record_usage handles non-existent token."""
    responses: list[Any] = [
        {"row": None},  # Token not found
    ]
    repo = make_repository(monkeypatch, responses)

    # Should not raise an error, just skip update
    await repo.record_usage("nonexistent", ip="127.0.0.1")


@pytest.mark.asyncio
async def test_postgres_service_token_repository_get_audit_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that get_audit_log retrieves audit entries."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {
            "rows": [
                {
                    "id": 1,
                    "token_id": "token-1",
                    "action": "created",
                    "timestamp": now,
                    "actor": "admin",
                    "ip": None,
                    "details": None,
                }
            ]
        },
    ]
    repo = make_repository(monkeypatch, responses)

    entries = await repo.get_audit_log("token-1", limit=100)

    assert len(entries) == 1
    assert entries[0]["action"] == "created"


@pytest.mark.asyncio
async def test_postgres_service_token_repository_record_audit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that record_audit_event creates an audit entry."""
    responses: list[Any] = [{}]  # INSERT service_token_audit_log
    repo = make_repository(monkeypatch, responses)

    await repo.record_audit_event(
        token_id="token-1",
        action="rotated",
        actor="admin",
        ip="127.0.0.1",
        details={"reason": "security"},
    )
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_service_token_repository_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that close shuts down the connection pool."""
    responses: list[Any] = []
    repo = make_repository(monkeypatch, responses)

    await repo.close()
    assert repo._pool is None


@pytest.mark.asyncio
async def test_postgres_service_token_repository_close_without_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that close works when pool is None."""
    monkeypatch.setattr(pg_repo, "_AsyncConnectionPool", object())
    monkeypatch.setattr(pg_repo, "_DictRowFactory", object())
    repo = PostgresServiceTokenRepository("postgresql://test")
    repo._pool = None

    await repo.close()
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_row_to_record_with_string_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles string timestamps correctly."""
    now_str = "2024-12-24T12:00:00+00:00"
    row = {
        "identifier": "token-1",
        "secret_hash": "hash-1",
        "scopes": json.dumps(["read", "write"]),
        "workspace_ids": json.dumps(["ws-1"]),
        "issued_at": now_str,  # String timestamp
        "expires_at": now_str,
        "rotation_expires_at": now_str,
        "revoked_at": now_str,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": now_str,
        "use_count": 0,
    }

    record = pg_repo._row_to_record(row)

    assert isinstance(record.issued_at, datetime)
    assert isinstance(record.expires_at, datetime)
    assert isinstance(record.rotation_expires_at, datetime)
    assert isinstance(record.revoked_at, datetime)
    assert record.scopes == frozenset({"read", "write"})
    assert record.workspace_ids == frozenset({"ws-1"})


@pytest.mark.asyncio
async def test_row_to_record_with_none_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles None values correctly."""
    row = {
        "identifier": "token-1",
        "secret_hash": "hash-1",
        "scopes": None,
        "workspace_ids": None,
        "issued_at": None,
        "expires_at": None,
        "rotation_expires_at": None,
        "revoked_at": None,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": None,
        "use_count": 0,
    }

    record = pg_repo._row_to_record(row)

    # When scopes and workspace_ids are None, they are converted to empty frozensets
    assert record.scopes == frozenset()
    assert record.workspace_ids == frozenset()
    assert record.issued_at is None
    assert record.expires_at is None
    assert record.revoked_at is None
    assert record.last_used_at is None


@pytest.mark.asyncio
async def test_row_to_record_with_list_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles list values for scopes."""
    row = {
        "identifier": "token-1",
        "secret_hash": "hash-1",
        "scopes": ["read", "write"],  # Direct list instead of JSON string
        "workspace_ids": ["ws-1", "ws-2"],
        "issued_at": None,
        "expires_at": None,
        "rotation_expires_at": None,
        "revoked_at": None,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": None,
        "use_count": 0,
    }

    record = pg_repo._row_to_record(row)

    assert record.scopes == frozenset({"read", "write"})
    assert record.workspace_ids == frozenset({"ws-1", "ws-2"})


@pytest.mark.asyncio
async def test_row_to_record_with_set_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles set values for scopes (covers line 95)."""
    row = {
        "identifier": "token-1",
        "secret_hash": "hash-1",
        "scopes": {"read", "write"},  # Direct set
        "workspace_ids": {"ws-1"},  # Direct set
        "issued_at": None,
        "expires_at": None,
        "rotation_expires_at": None,
        "revoked_at": None,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": None,
        "use_count": 0,
    }

    record = pg_repo._row_to_record(row)

    assert record.scopes == frozenset({"read", "write"})
    assert record.workspace_ids == frozenset({"ws-1"})


@pytest.mark.asyncio
async def test_row_to_record_with_datetime_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles datetime objects directly."""
    now = datetime.now(tz=UTC)
    row = {
        "identifier": "token-1",
        "secret_hash": "hash-1",
        "scopes": None,
        "workspace_ids": None,
        "issued_at": now,  # Direct datetime object
        "expires_at": now,
        "rotation_expires_at": now,
        "revoked_at": now,
        "revocation_reason": None,
        "rotated_to": None,
        "last_used_at": now,
        "use_count": 0,
    }

    record = pg_repo._row_to_record(row)

    assert record.issued_at == now
    assert record.expires_at == now
    assert record.rotation_expires_at == now
    assert record.revoked_at == now


@pytest.mark.asyncio
async def test_postgres_service_token_repository_connection_error_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that connection errors are properly handled and rolled back."""

    class FailingConnection(FakeConnection):
        async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
            self.queries.append((query.strip(), params))
            raise Exception("Connection error")

    responses: list[Any] = []
    repo = make_repository(monkeypatch, responses)
    repo._pool = FakePool(FailingConnection(responses))

    # Should raise exception and rollback
    with pytest.raises(Exception, match="Connection error"):
        await repo.list_all()

    # Verify rollback was called
    assert repo._pool._connection.rollbacks == 1


@pytest.mark.asyncio
async def test_postgres_service_token_repository_get_pool_race_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_pool handles race conditions correctly (covers line 146)."""

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any):
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(pg_repo, "_AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_repo, "_DictRowFactory", lambda x: x)
    repo = PostgresServiceTokenRepository("postgresql://test")

    # Simulate race: another coroutine sets _pool while we're waiting for lock
    class RacyPoolLock:
        async def __aenter__(self) -> None:
            # Simulate another coroutine creating the pool while we wait for the lock
            if repo._pool is None:
                fake_pool = FakeAsyncConnectionPool()
                await fake_pool.open()
                repo._pool = fake_pool

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

    repo._pool_lock = RacyPoolLock()  # type: ignore

    pool = await repo._get_pool()
    # Should return the existing pool that was set during lock acquisition (line 146)
    assert pool.opened is True


@pytest.mark.asyncio
async def test_postgres_service_token_repository_ensure_initialized_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_initialized handles race conditions (covers line 183)."""
    responses: list[Any] = []
    for _ in range(6):  # Schema DDL statements
        responses.append({})

    repo = make_repository(monkeypatch, responses, initialized=False)

    # Simulate race: another coroutine initializes while we're waiting for lock
    class RacySchemaLock:
        async def __aenter__(self) -> None:
            # Simulate another coroutine completing initialization while
            # we wait for the lock
            repo._initialized = True

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

    repo._schema_lock = RacySchemaLock()  # type: ignore

    await repo._ensure_initialized()
    # Should return early from line 183 without running schema statements
    assert repo._initialized is True


@pytest.mark.asyncio
async def test_postgres_service_token_repository_record_usage_with_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that record_usage saves both ip and user_agent in details."""
    responses: list[Any] = [
        {},  # UPDATE service_tokens
        {},  # INSERT audit_log
    ]
    repo = make_repository(monkeypatch, responses)

    await repo.record_usage("token-1", ip="192.168.1.1", user_agent="Mozilla/5.0")

    # Verify both UPDATE and INSERT were called
    connection = repo._pool._connection
    assert len(connection.queries) == 3
    assert "SELECT tenant_id FROM service_tokens" in connection.queries[0][0]


@pytest.mark.asyncio
async def test_postgres_service_token_repository_record_usage_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that record_usage works without ip/user_agent."""
    responses: list[Any] = [
        {},  # UPDATE service_tokens
        {},  # INSERT audit_log
    ]
    repo = make_repository(monkeypatch, responses)

    await repo.record_usage("token-1")

    # Verify both UPDATE and INSERT were called
    connection = repo._pool._connection
    assert len(connection.queries) == 3
    assert "SELECT tenant_id FROM service_tokens" in connection.queries[0][0]
