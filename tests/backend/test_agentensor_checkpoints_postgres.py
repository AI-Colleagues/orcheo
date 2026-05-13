"""Tests for PostgresAgentensorCheckpointStore using mocks."""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from orcheo.agentensor.checkpoints import (
    AgentensorCheckpointNotFoundError,
)
from orcheo_backend.app.agentensor import checkpoint_store as checkpoint_store_module
from orcheo_backend.app.agentensor.checkpoint_store import (
    POSTGRES_CHECKPOINT_MIGRATION,
    PostgresAgentensorCheckpointStore,
)


# --- Mocks ---


class FakeRow(dict[str, Any]):
    """Fake row that supports both index and key access."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self, *, row: dict[str, Any] | None = None, rows: list[Any] | None = None
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]
        self._idx = 0

    async def fetchone(self) -> FakeRow | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake database connection."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        if not self._responses:
            return FakeCursor()
        response = self._responses.pop(0)
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            # check if it has "row" or "rows" keys special handling or just is the row
            if "row" in response or "rows" in response:
                return FakeCursor(row=response.get("row"), rows=response.get("rows"))
            return FakeCursor(row=response)
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakePool:
    """Fake connection pool."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.closed = False

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    def connection(self) -> FakeConnection:
        return self._connection


@pytest.fixture
def mock_postgres_deps(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_pool_cls = MagicMock()
    # Configure the instance returned by the class
    pool_instance = mock_pool_cls.return_value
    pool_instance.open = AsyncMock()
    pool_instance.close = AsyncMock()

    # We also need context manager for connection()
    # pool.connection() -> context manager -> yields connection
    mock_conn = MagicMock()
    mock_conn.commit = AsyncMock()
    mock_conn.rollback = AsyncMock()

    # mocking connection context manager
    # async with pool.connection() as conn:
    pool_instance.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool_instance.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_dict_row = MagicMock()
    monkeypatch.setattr(checkpoint_store_module, "_AsyncConnectionPool", mock_pool_cls)
    monkeypatch.setattr(checkpoint_store_module, "_DictRowFactory", mock_dict_row)
    return mock_pool_cls


def make_store(
    mock_postgres_deps: MagicMock, responses: list[Any], *, initialized: bool = True
) -> PostgresAgentensorCheckpointStore:
    store = PostgresAgentensorCheckpointStore("postgresql://test")
    # Mock the pool
    store._pool = FakePool(FakeConnection(responses))
    store._initialized = initialized
    return store


# --- Tests ---


@pytest.mark.asyncio
async def test_init_raises_without_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checkpoint_store_module, "_AsyncConnectionPool", None)
    with pytest.raises(RuntimeError, match="requires psycopg"):
        PostgresAgentensorCheckpointStore("postgresql://test")


@pytest.mark.asyncio
async def test_ensure_initialized(mock_postgres_deps: MagicMock) -> None:
    # responses for migration: just empty results for executes
    responses: list[Any] = [
        {} for _ in POSTGRES_CHECKPOINT_MIGRATION.split(";") if _.strip()
    ]
    store = make_store(mock_postgres_deps, responses, initialized=False)

    # We need to hack the pool into place because _ensure_initialized calls
    # _connection() which calls _get_pool()
    # But _get_pool returns self._pool if not None.
    # But we set self._pool in make_store.

    await store._ensure_initialized()
    assert store._initialized
    conn = store._pool._connection  # type: ignore
    assert len(conn.queries) > 0  # Should have executed migrations


@pytest.mark.asyncio
async def test_ensure_initialized_idempotent(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [], initialized=True)
    await store._ensure_initialized()
    conn = store._pool._connection  # type: ignore
    assert len(conn.queries) == 0


@pytest.mark.asyncio
async def test_record_checkpoint(mock_postgres_deps: MagicMock) -> None:
    workflow_id = "wf1"

    # 1. _resolve_version query response (max version)
    # 2. INSERT query response
    responses: list[Any] = [
        {"max_version": 5},
        {},
    ]
    store = make_store(mock_postgres_deps, responses)

    cp = await store.record_checkpoint(
        workflow_id=workflow_id,
        runnable_config={"a": 1},
        metrics={"score": 0.5},
    )

    assert cp.config_version == 6
    conn = store._pool._connection  # type: ignore
    assert conn.commits >= 1
    # Verify insert happened
    insert_query = next((q for q in conn.queries if "INSERT" in q[0]), None)
    assert insert_query is not None


@pytest.mark.asyncio
async def test_record_checkpoint_is_best(mock_postgres_deps: MagicMock) -> None:
    workflow_id = "wf1"

    responses: list[Any] = [
        {"max_version": 5},
        {},  # INSERT
        {},  # UPDATE old best
    ]
    store = make_store(mock_postgres_deps, responses)

    await store.record_checkpoint(
        workflow_id=workflow_id, runnable_config={}, metrics={}, is_best=True
    )

    conn = store._pool._connection  # type: ignore
    # Verify update happened
    update_query = next((q for q in conn.queries if "UPDATE" in q[0]), None)
    assert update_query is not None


@pytest.mark.asyncio
async def test_list_checkpoints(mock_postgres_deps: MagicMock) -> None:
    now_str = datetime.now(UTC).isoformat()
    db_rows = [
        {
            "id": "cp1",
            "workflow_id": "wf1",
            "config_version": 1,
            "runnable_config": "{}",
            "metrics": "{}",
            "metadata": "{}",
            "artifact_url": None,
            "is_best": False,
            "created_at": now_str,
        }
    ]

    store = make_store(mock_postgres_deps, [db_rows])

    checkpoints = await store.list_checkpoints("wf1", limit=10)
    assert len(checkpoints) == 1
    assert checkpoints[0].id == "cp1"

    conn = store._pool._connection  # type: ignore
    assert "LIMIT" in conn.queries[0][0]


@pytest.mark.asyncio
async def test_get_checkpoint(mock_postgres_deps: MagicMock) -> None:
    now_str = datetime.now(UTC).isoformat()
    db_row = {
        "id": "cp1",
        "workflow_id": "wf1",
        "config_version": 1,
        "runnable_config": "{}",
        "metrics": "{}",
        "metadata": "{}",
        "artifact_url": None,
        "is_best": False,
        "created_at": now_str,
    }

    store = make_store(mock_postgres_deps, [{"row": db_row}])

    cp = await store.get_checkpoint("cp1")
    assert cp.id == "cp1"


@pytest.mark.asyncio
async def test_get_checkpoint_not_found(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [None])  # Returns None for fetchone

    with pytest.raises(AgentensorCheckpointNotFoundError):
        await store.get_checkpoint("missing")


@pytest.mark.asyncio
async def test_latest_checkpoint(mock_postgres_deps: MagicMock) -> None:
    now_str = datetime.now(UTC).isoformat()
    db_rows = [
        {
            "id": "cp2",
            "workflow_id": "wf1",
            "config_version": 2,
            "runnable_config": "{}",
            "metrics": "{}",
            "metadata": "{}",
            "artifact_url": None,
            "is_best": True,
            "created_at": now_str,
        }
    ]
    store = make_store(mock_postgres_deps, [db_rows])  # list_checkpoints calls fetchall

    cp = await store.latest_checkpoint("wf1")
    assert cp is not None
    assert cp.config_version == 2


@pytest.mark.asyncio
async def test_resolve_version_provided(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [])
    # _resolve_version called internally, but we can access it via private method
    # if needed
    # but let's test via record_checkpoint with explicit version

    # We need to simulate that record_checkpoint calls _resolve_version
    # If provided_version is set, it returns it immediately without query.
    # Then it does INSERT.

    responses: list[Any] = [{}]  # INSERT
    store._pool._connection._responses = list(responses)  # reset responses

    cp = await store.record_checkpoint(
        workflow_id="wf1", runnable_config={}, metrics={}, config_version=99
    )
    assert cp.config_version == 99
    conn = store._pool._connection  # type: ignore
    # Only INSERT query, no SELECT MAX
    assert len(conn.queries) == 1
    assert "INSERT" in conn.queries[0][0]


@pytest.mark.asyncio
async def test_close(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [])
    pool_ref = store._pool
    await store.close()
    assert pool_ref.closed  # type: ignore
    assert store._pool is None


@pytest.mark.asyncio
async def test_get_pool_creates_new(mock_postgres_deps: MagicMock) -> None:
    # Test that _get_pool creates a pool if one doesn't exist
    store = PostgresAgentensorCheckpointStore("postgresql://test")
    # store._pool is None initially

    pool = await store._get_pool()
    assert pool is not None
    assert mock_postgres_deps.called


@pytest.mark.asyncio
async def test_get_pool_race_condition(mock_postgres_deps: MagicMock) -> None:
    store = PostgresAgentensorCheckpointStore("postgresql://test")

    # Simulate race condition: _pool is None initially, but set by another task
    # while waiting for lock

    async def mock_lock_acquire():
        # When lock is acquired, set the pool to simulate another task finishing first
        store._pool = MagicMock()
        return True

    # We can't easily patch the lock's __aenter__, so we'll do this:
    # We will wrap the lock to perform the side effect

    class SideEffectLock:
        async def __aenter__(self):
            store._pool = MagicMock()

        async def __aexit__(self, *args):
            pass

    store._init_lock = SideEffectLock()  # type: ignore

    pool = await store._get_pool()
    assert pool == store._pool


@pytest.mark.asyncio
async def test_ensure_initialized_race_condition(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [], initialized=False)

    class SideEffectLock:
        async def __aenter__(self):
            store._initialized = True

        async def __aexit__(self, *args):
            pass

    store._init_lock = SideEffectLock()  # type: ignore

    await store._ensure_initialized()
    # Should return early without running migrations
    # If it ran migrations, we would crash because we didn't provide responses for them
    assert store._initialized


@pytest.mark.asyncio
async def test_connection_rollback(mock_postgres_deps: MagicMock) -> None:
    store = make_store(mock_postgres_deps, [])

    with pytest.raises(ValueError):
        async with store._connection() as conn:
            raise ValueError("boom")

    conn = store._pool._connection  # type: ignore
    assert conn.rollbacks == 1


@pytest.mark.asyncio
async def test_list_checkpoints_no_limit(mock_postgres_deps: MagicMock) -> None:
    now_str = datetime.now(UTC).isoformat()
    db_rows = [
        {
            "id": "cp1",
            "workflow_id": "wf1",
            "config_version": 1,
            "runnable_config": "{}",
            "metrics": "{}",
            "metadata": "{}",
            "artifact_url": None,
            "is_best": False,
            "created_at": now_str,
        }
    ]
    store = make_store(mock_postgres_deps, [db_rows])

    await store.list_checkpoints("wf1", limit=None)

    conn = store._pool._connection  # type: ignore
    assert "LIMIT" not in conn.queries[0][0]


@pytest.mark.asyncio
async def test_list_checkpoints_with_workspace_filter(
    mock_postgres_deps: MagicMock,
) -> None:
    """Verify list_checkpoints scopes queries to a workspace when requested."""

    now_str = datetime.now(UTC).isoformat()
    db_rows = [
        {
            "id": "cp1",
            "workflow_id": "wf1",
            "workspace_id": "workspace-1",
            "config_version": 1,
            "runnable_config": "{}",
            "metrics": "{}",
            "metadata": "{}",
            "artifact_url": None,
            "is_best": False,
            "created_at": now_str,
        }
    ]
    store = make_store(mock_postgres_deps, [db_rows])

    checkpoints = await store.list_checkpoints(
        "wf1",
        workspace_id="workspace-1",
    )

    conn = store._pool._connection  # type: ignore
    assert "workspace_id = %s" in conn.queries[0][0]
    assert conn.queries[0][1] == ("wf1", "workspace-1")
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_row_conversion_pre_parsed() -> None:
    # Test _row_to_checkpoint dealing with non-string types (already parsed)
    now = datetime.now(UTC)
    config = {"a": 1}
    row = {
        "id": "cp1",
        "workflow_id": "wf1",
        "config_version": 1,
        "runnable_config": config,  # Already dict
        "metrics": config,  # Already dict
        "metadata": config,  # Already dict
        "artifact_url": "url",
        "is_best": True,
        "created_at": now,  # Already datetime
    }

    cp = PostgresAgentensorCheckpointStore._row_to_checkpoint(row)
    assert cp.runnable_config == config
    assert cp.created_at == now


@pytest.mark.asyncio
async def test_close_idempotent() -> None:
    store = PostgresAgentensorCheckpointStore("postgresql://test")
    store._pool = None
    await store.close()  # Should not error
