"""Tests for the PostgreSQL-backed history store.

These tests use in-memory fakes to verify repository behavior without requiring
a real PostgreSQL database connection.
"""

from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import pytest
from orcheo_backend.app.history import postgres_store as pg_store
from orcheo_backend.app.history.models import (
    RunHistoryError,
    RunHistoryNotFoundError,
)
from orcheo_backend.app.history.postgres_store import PostgresRunHistoryStore


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


@dataclass
class _EmbeddingLike:
    values: list[float]


def make_store(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresRunHistoryStore:
    """Create a PostgresRunHistoryStore with fake connection pool."""
    monkeypatch.setattr(pg_store, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_store, "DictRowFactory", object())
    store = PostgresRunHistoryStore("postgresql://test")
    store._pool = FakePool(FakeConnection(responses))
    store._initialized = initialized
    return store


@pytest.mark.asyncio
async def test_postgres_store_dependency_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that missing psycopg dependency raises RuntimeError."""
    monkeypatch.setattr(pg_store, "AsyncConnectionPool", None)
    monkeypatch.setattr(pg_store, "DictRowFactory", None)

    with pytest.raises(RuntimeError, match="psycopg"):
        PostgresRunHistoryStore("postgresql://test")


@pytest.mark.asyncio
async def test_postgres_store_get_pool_creates_on_first_call(
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

    monkeypatch.setattr(pg_store, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_store, "DictRowFactory", lambda x: x)
    store = PostgresRunHistoryStore(
        "postgresql://test",
        pool_min_size=2,
        pool_max_size=20,
        pool_timeout=10.0,
        pool_max_idle=100.0,
    )

    pool = await store._get_pool()
    assert pool.opened is True
    assert pool.args[0] == "postgresql://test"

    # Repeated call returns same pool
    pool2 = await store._get_pool()
    assert pool2 is pool


@pytest.mark.asyncio
async def test_postgres_store_ensure_initialized_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_initialized only runs once."""
    responses: list[Any] = []
    # Schema creation DDL statements count
    for _ in range(4):  # CREATE TABLE, CREATE INDEX, CREATE TABLE, CREATE INDEX
        responses.append({})

    store = make_store(monkeypatch, responses, initialized=False)

    # Multiple concurrent calls
    await asyncio.gather(
        store._ensure_initialized(),
        store._ensure_initialized(),
        store._ensure_initialized(),
    )

    assert store._initialized is True


@pytest.mark.asyncio
async def test_postgres_store_start_run_basic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that start_run creates a new run record."""
    responses: list[Any] = [{}]  # INSERT execution_history
    store = make_store(monkeypatch, responses)

    record = await store.start_run(
        workflow_id="wf-123",
        execution_id="exec-456",
        inputs={"key": "value"},
        runnable_config={"config_key": "config_value"},
        tags=["tag1", "tag2"],
        callbacks=["callback1"],
        metadata={"meta_key": "meta_value"},
        run_name="test-run",
        trace_id="trace-123",
    )

    assert record.workflow_id == "wf-123"
    assert record.execution_id == "exec-456"
    assert record.inputs == {"key": "value"}
    assert record.runnable_config == {"config_key": "config_value"}
    assert record.tags == ["tag1", "tag2"]
    assert record.callbacks == ["callback1"]
    assert record.metadata == {"meta_key": "meta_value"}
    assert record.run_name == "test-run"
    assert record.trace_id == "trace-123"
    assert record.status == "running"
    assert record.steps == []


@pytest.mark.asyncio
async def test_postgres_store_start_run_with_pydantic_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that start_run handles Pydantic-like runnable_config."""

    class FakeConfig:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            return {"dumped": True, "mode": mode}

    responses: list[Any] = [{}]
    store = make_store(monkeypatch, responses)

    record = await store.start_run(
        workflow_id="wf-123",
        execution_id="exec-456",
        runnable_config=FakeConfig(),  # type: ignore[arg-type]
    )

    assert record.runnable_config == {"dumped": True, "mode": "json"}


@pytest.mark.asyncio
async def test_postgres_store_start_run_extracts_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that start_run extracts values from runnable_config."""
    responses: list[Any] = [{}]
    store = make_store(monkeypatch, responses)

    record = await store.start_run(
        workflow_id="wf-123",
        execution_id="exec-456",
        runnable_config={
            "tags": ["config-tag"],
            "callbacks": ["config-callback"],
            "metadata": {"config-meta": "value"},
            "run_name": "config-run-name",
        },
    )

    # Values from config are used if not explicitly provided
    assert record.tags == ["config-tag"]
    assert record.callbacks == ["config-callback"]
    assert record.metadata == {"config-meta": "value"}
    assert record.run_name == "config-run-name"


@pytest.mark.asyncio
async def test_postgres_store_start_run_wraps_non_list_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[Any] = [{}]
    store = make_store(monkeypatch, responses)

    original_normalize = pg_store.normalize_json_value

    def _normalize_with_scalar(value: Any) -> Any:
        if value == ["config-callback"]:
            return "config-callback"
        return original_normalize(value)

    monkeypatch.setattr(pg_store, "normalize_json_value", _normalize_with_scalar)

    record = await store.start_run(
        workflow_id="wf-123",
        execution_id="exec-456",
        runnable_config={"callbacks": ["config-callback"]},
    )

    assert record.callbacks == ["config-callback"]


@pytest.mark.asyncio
async def test_postgres_store_start_run_duplicate_raises_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that starting a duplicate run raises RunHistoryError."""

    class DuplicateKeyError(Exception):
        def __str__(self) -> str:
            return "duplicate key value violates unique constraint"

    # Mock connection to raise duplicate key error
    class ErrorConnection(FakeConnection):
        async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
            if "INSERT INTO execution_history" in query:
                raise DuplicateKeyError()
            return await super().execute(query, params)

    monkeypatch.setattr(pg_store, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_store, "DictRowFactory", object())
    store = PostgresRunHistoryStore("postgresql://test")
    store._pool = FakePool(ErrorConnection([]))
    store._initialized = True

    with pytest.raises(RunHistoryError, match="already exists"):
        await store.start_run(
            workflow_id="wf-123",
            execution_id="exec-456",
        )


@pytest.mark.asyncio
async def test_postgres_store_append_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that append_step adds a step to the execution."""
    responses: list[Any] = [
        {"row": {"execution_id": "exec-456"}},  # _fetch_record
        {"row": {"current_index": 0}},  # SELECT MAX(step_index)
        {},  # INSERT execution_history_steps
        {},  # UPDATE trace_last_span_at
    ]
    store = make_store(monkeypatch, responses)

    step = await store.append_step(
        execution_id="exec-456",
        payload={"step_type": "test", "data": "value"},
    )

    assert step.index == 1
    assert step.payload == {"step_type": "test", "data": "value"}


@pytest.mark.asyncio
async def test_postgres_store_append_step_normalizes_non_json_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[Any] = [
        {"row": {"execution_id": "exec-456"}},
        {"row": {"current_index": -1}},
        {},
        {},
    ]
    store = make_store(monkeypatch, responses)

    step = await store.append_step(
        execution_id="exec-456",
        payload={"embedding": _EmbeddingLike(values=[0.1, 0.2])},
    )

    assert step.payload == {"embedding": {"values": [0.1, 0.2]}}
    queries = store._pool._connection.queries  # type: ignore[union-attr]
    insert_params = next(
        params
        for query, params in queries
        if "INSERT INTO execution_history_steps" in query
    )
    assert insert_params is not None
    persisted_payload = json.loads(insert_params[3])
    assert persisted_payload == {"embedding": {"values": [0.1, 0.2]}}


@pytest.mark.asyncio
async def test_postgres_store_append_step_first_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that append_step works for the first step (no existing steps)."""
    responses: list[Any] = [
        {"row": {"execution_id": "exec-456"}},  # _fetch_record
        {"row": {"current_index": -1}},  # SELECT MAX(step_index) - no steps yet
        {},  # INSERT execution_history_steps
        {},  # UPDATE trace_last_span_at
    ]
    store = make_store(monkeypatch, responses)

    step = await store.append_step(
        execution_id="exec-456",
        payload={"step_type": "first"},
    )

    assert step.index == 0


@pytest.mark.asyncio
async def test_postgres_store_append_step_not_found_raises_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that appending step to non-existent execution raises error."""
    responses: list[Any] = [
        {"row": None},  # _fetch_record returns None
    ]
    store = make_store(monkeypatch, responses)

    with pytest.raises(RunHistoryNotFoundError, match="not found"):
        await store.append_step(
            execution_id="nonexistent",
            payload={"step_type": "test"},
        )


@pytest.mark.asyncio
async def test_postgres_store_mark_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that mark_completed updates execution status."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {"rowcount": 1},  # UPDATE execution_history
        {  # _fetch_record
            "row": {
                "execution_id": "exec-456",
                "workflow_id": "wf-123",
                "inputs": json.dumps({}),
                "runnable_config": json.dumps({}),
                "tags": json.dumps([]),
                "callbacks": json.dumps([]),
                "metadata": json.dumps({}),
                "run_name": None,
                "status": "completed",
                "started_at": now,
                "completed_at": now,
                "error": None,
                "trace_id": None,
                "trace_started_at": now,
                "trace_completed_at": now,
                "trace_last_span_at": now,
            }
        },
        {"rows": []},  # _fetch_steps
    ]
    store = make_store(monkeypatch, responses)

    record = await store.mark_completed(execution_id="exec-456")

    assert record.status == "completed"
    assert record.error is None
    assert record.completed_at is not None


@pytest.mark.asyncio
async def test_postgres_store_mark_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that mark_failed updates execution with error."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {"rowcount": 1},  # UPDATE execution_history
        {  # _fetch_record
            "row": {
                "execution_id": "exec-456",
                "workflow_id": "wf-123",
                "inputs": json.dumps({}),
                "runnable_config": json.dumps({}),
                "tags": json.dumps([]),
                "callbacks": json.dumps([]),
                "metadata": json.dumps({}),
                "run_name": None,
                "status": "error",
                "started_at": now,
                "completed_at": now,
                "error": "Test error",
                "trace_id": None,
                "trace_started_at": now,
                "trace_completed_at": now,
                "trace_last_span_at": now,
            }
        },
        {"rows": []},  # _fetch_steps
    ]
    store = make_store(monkeypatch, responses)

    record = await store.mark_failed(
        execution_id="exec-456",
        error="Test error",
    )

    assert record.status == "error"
    assert record.error == "Test error"


@pytest.mark.asyncio
async def test_postgres_store_mark_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that mark_cancelled updates execution with cancellation."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {"rowcount": 1},  # UPDATE execution_history
        {  # _fetch_record
            "row": {
                "execution_id": "exec-456",
                "workflow_id": "wf-123",
                "inputs": json.dumps({}),
                "runnable_config": json.dumps({}),
                "tags": json.dumps([]),
                "callbacks": json.dumps([]),
                "metadata": json.dumps({}),
                "run_name": None,
                "status": "cancelled",
                "started_at": now,
                "completed_at": now,
                "error": "User requested",
                "trace_id": None,
                "trace_started_at": now,
                "trace_completed_at": now,
                "trace_last_span_at": now,
            }
        },
        {"rows": []},  # _fetch_steps
    ]
    store = make_store(monkeypatch, responses)

    record = await store.mark_cancelled(
        execution_id="exec-456",
        reason="User requested",
    )

    assert record.status == "cancelled"
    assert record.error == "User requested"


@pytest.mark.asyncio
async def test_postgres_store_update_status_not_found_raises_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that updating status of non-existent execution raises error."""
    responses: list[Any] = [
        {"rowcount": 0},  # UPDATE execution_history - no rows affected
    ]
    store = make_store(monkeypatch, responses)

    with pytest.raises(RunHistoryNotFoundError, match="not found"):
        await store._update_status(
            execution_id="nonexistent",
            status="completed",
            error=None,
        )


@pytest.mark.asyncio
async def test_postgres_store_get_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that get_history retrieves a complete record."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {  # _fetch_record
            "row": {
                "execution_id": "exec-456",
                "workflow_id": "wf-123",
                "inputs": json.dumps({"key": "value"}),
                "runnable_config": json.dumps({"config": "data"}),
                "tags": json.dumps(["tag1"]),
                "callbacks": json.dumps(["cb1"]),
                "metadata": json.dumps({"meta": "data"}),
                "run_name": "test-run",
                "status": "completed",
                "started_at": now,
                "completed_at": now,
                "error": None,
                "trace_id": "trace-123",
                "trace_started_at": now,
                "trace_completed_at": now,
                "trace_last_span_at": now,
            }
        },
        {  # _fetch_steps
            "rows": [
                {
                    "step_index": 0,
                    "at": now,
                    "payload": json.dumps({"step": "data"}),
                }
            ]
        },
    ]
    store = make_store(monkeypatch, responses)

    record = await store.get_history(execution_id="exec-456")

    assert record.execution_id == "exec-456"
    assert record.workflow_id == "wf-123"
    assert record.inputs == {"key": "value"}
    assert record.run_name == "test-run"
    assert len(record.steps) == 1
    assert record.steps[0].payload == {"step": "data"}


@pytest.mark.asyncio
async def test_postgres_store_get_history_not_found_raises_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that getting non-existent history raises error."""
    responses: list[Any] = [
        {"row": None},  # _fetch_record returns None
    ]
    store = make_store(monkeypatch, responses)

    with pytest.raises(RunHistoryNotFoundError, match="not found"):
        await store.get_history(execution_id="nonexistent")


@pytest.mark.asyncio
async def test_postgres_store_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that clear removes all history data."""
    responses: list[Any] = [
        {},  # DELETE FROM execution_history_steps
        {},  # DELETE FROM execution_history
    ]
    store = make_store(monkeypatch, responses)

    await store.clear()
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_store_list_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that list_histories retrieves all histories for a workflow."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {  # Main query
            "rows": [
                {
                    "execution_id": "exec-1",
                    "workflow_id": "wf-123",
                    "inputs": json.dumps({}),
                    "runnable_config": json.dumps({}),
                    "tags": json.dumps([]),
                    "callbacks": json.dumps([]),
                    "metadata": json.dumps({}),
                    "run_name": None,
                    "status": "completed",
                    "started_at": now,
                    "completed_at": now,
                    "error": None,
                    "trace_id": None,
                    "trace_started_at": now,
                    "trace_completed_at": now,
                    "trace_last_span_at": now,
                },
                {
                    "execution_id": "exec-2",
                    "workflow_id": "wf-123",
                    "inputs": json.dumps({}),
                    "runnable_config": json.dumps({}),
                    "tags": json.dumps([]),
                    "callbacks": json.dumps([]),
                    "metadata": json.dumps({}),
                    "run_name": None,
                    "status": "running",
                    "started_at": now,
                    "completed_at": None,
                    "error": None,
                    "trace_id": None,
                    "trace_started_at": now,
                    "trace_completed_at": None,
                    "trace_last_span_at": now,
                },
            ]
        },
        {"rows": []},  # _fetch_steps for exec-1
        {"rows": []},  # _fetch_steps for exec-2
    ]
    store = make_store(monkeypatch, responses)

    records = await store.list_histories(workflow_id="wf-123")

    assert len(records) == 2
    assert records[0].execution_id == "exec-1"
    assert records[1].execution_id == "exec-2"


@pytest.mark.asyncio
async def test_postgres_store_list_histories_with_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that list_histories respects the limit parameter."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {  # Main query with LIMIT
            "rows": [
                {
                    "execution_id": "exec-1",
                    "workflow_id": "wf-123",
                    "inputs": json.dumps({}),
                    "runnable_config": json.dumps({}),
                    "tags": json.dumps([]),
                    "callbacks": json.dumps([]),
                    "metadata": json.dumps({}),
                    "run_name": None,
                    "status": "completed",
                    "started_at": now,
                    "completed_at": now,
                    "error": None,
                    "trace_id": None,
                    "trace_started_at": now,
                    "trace_completed_at": now,
                    "trace_last_span_at": now,
                },
            ]
        },
        {"rows": []},  # _fetch_steps
    ]
    store = make_store(monkeypatch, responses)

    records = await store.list_histories(workflow_id="wf-123", limit=1)

    assert len(records) == 1


@pytest.mark.asyncio
async def test_postgres_store_list_histories_with_workspace_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that list_histories includes the workspace filter when provided."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {
            "rows": [
                {
                    "execution_id": "exec-1",
                    "workflow_id": "wf-123",
                    "workspace_id": "workspace-1",
                    "inputs": json.dumps({}),
                    "runnable_config": json.dumps({}),
                    "tags": json.dumps([]),
                    "callbacks": json.dumps([]),
                    "metadata": json.dumps({}),
                    "run_name": None,
                    "status": "completed",
                    "started_at": now,
                    "completed_at": now,
                    "error": None,
                    "trace_id": None,
                    "trace_started_at": now,
                    "trace_completed_at": now,
                    "trace_last_span_at": now,
                }
            ]
        },
        {"rows": []},
    ]
    store = make_store(monkeypatch, responses)

    records = await store.list_histories(
        workflow_id="wf-123",
        workspace_id="workspace-1",
    )

    conn = store._pool._connection  # type: ignore[union-attr]
    assert "workspace_id = %s" in conn.queries[0][0]
    assert conn.queries[0][1] == ("wf-123", "workspace-1")
    assert len(records) == 1


@pytest.mark.asyncio
async def test_postgres_store_fetch_steps_with_string_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _fetch_steps handles string timestamps correctly."""
    now_str = "2024-12-24T12:00:00+00:00"
    responses: list[Any] = [
        {
            "rows": [
                {
                    "step_index": 0,
                    "at": now_str,  # String timestamp
                    "payload": '{"key": "value"}',  # String JSON
                }
            ]
        },
    ]
    store = make_store(monkeypatch, responses)

    async with store._connection() as conn:
        steps = await store._fetch_steps(conn, "exec-456")

    assert len(steps) == 1
    assert isinstance(steps[0].at, datetime)
    assert steps[0].payload == {"key": "value"}


@pytest.mark.asyncio
async def test_postgres_store_row_to_record_with_string_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record handles string timestamps and JSON."""
    now_str = "2024-12-24T12:00:00+00:00"
    row = {
        "execution_id": "exec-456",
        "workflow_id": "wf-123",
        "inputs": '{"key": "value"}',  # String JSON
        "runnable_config": '{"config": "data"}',
        "tags": '["tag1"]',
        "callbacks": '["cb1"]',
        "metadata": '{"meta": "data"}',
        "run_name": "test-run",
        "status": "completed",
        "started_at": now_str,  # String timestamp
        "completed_at": now_str,
        "error": None,
        "trace_id": "trace-123",
        "trace_started_at": now_str,
        "trace_completed_at": now_str,
        "trace_last_span_at": now_str,
    }

    record = PostgresRunHistoryStore._row_to_record(row, [])

    assert isinstance(record.started_at, datetime)
    assert isinstance(record.completed_at, datetime)
    assert record.inputs == {"key": "value"}
    assert record.tags == ["tag1"]


@pytest.mark.asyncio
async def test_postgres_store_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that close shuts down the connection pool."""
    responses: list[Any] = []
    store = make_store(monkeypatch, responses)

    await store.close()
    assert store._pool is None


@pytest.mark.asyncio
async def test_postgres_store_close_without_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that close works when pool is None."""
    monkeypatch.setattr(pg_store, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_store, "DictRowFactory", object())
    store = PostgresRunHistoryStore("postgresql://test")
    store._pool = None

    await store.close()
    # Just verify it doesn't raise an error


@pytest.mark.asyncio
async def test_postgres_store_update_status_sets_trace_last_span_at_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _update_status sets trace_last_span_at when it's None."""
    now = datetime.now(tz=UTC).isoformat()
    responses: list[Any] = [
        {"rowcount": 1},  # UPDATE execution_history
        {  # _fetch_record
            "row": {
                "execution_id": "exec-456",
                "workflow_id": "wf-123",
                "inputs": json.dumps({}),
                "runnable_config": json.dumps({}),
                "tags": json.dumps([]),
                "callbacks": json.dumps([]),
                "metadata": json.dumps({}),
                "run_name": None,
                "status": "completed",
                "started_at": now,
                "completed_at": now,
                "error": None,
                "trace_id": None,
                "trace_started_at": now,
                "trace_completed_at": now,
                "trace_last_span_at": None,  # None
            }
        },
        {"rows": []},  # _fetch_steps
    ]
    store = make_store(monkeypatch, responses)

    record = await store._update_status(
        execution_id="exec-456",
        status="completed",
        error=None,
    )

    assert record.trace_last_span_at is not None


@pytest.mark.asyncio
async def test_postgres_store_get_pool_race_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _get_pool handles race condition properly."""

    class FakeAsyncConnectionPool:
        def __init__(self, *args: Any, **kwargs: Any):
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    monkeypatch.setattr(pg_store, "AsyncConnectionPool", FakeAsyncConnectionPool)
    monkeypatch.setattr(pg_store, "DictRowFactory", lambda x: x)
    store = PostgresRunHistoryStore("postgresql://test")

    # Simulate race condition: pool becomes non-None while acquiring lock
    class SideEffectLock:
        async def __aenter__(self) -> None:
            # Set pool before creating new one
            store._pool = "existing_pool"  # type: ignore[assignment]

        async def __aexit__(self, *args: Any) -> None:
            pass

    store._pool_lock = SideEffectLock()  # type: ignore[assignment]
    store._pool = None

    pool = await store._get_pool()
    assert pool == "existing_pool"


@pytest.mark.asyncio
async def test_postgres_store_ensure_initialized_race_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_initialized handles race condition properly."""
    responses: list[Any] = []
    store = make_store(monkeypatch, responses, initialized=False)

    # Simulate race condition: initialized becomes True while acquiring lock
    class SideEffectLock:
        async def __aenter__(self) -> None:
            # Set initialized before schema execution
            store._initialized = True

        async def __aexit__(self, *args: Any) -> None:
            pass

    store._schema_lock = SideEffectLock()  # type: ignore[assignment]
    store._initialized = False

    await store._ensure_initialized()
    assert store._initialized is True


@pytest.mark.asyncio
async def test_postgres_store_row_to_record_parse_ts_with_datetime_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record parse_ts handles datetime objects."""
    now = datetime.now(tz=UTC)
    row = {
        "execution_id": "exec-456",
        "workflow_id": "wf-123",
        "inputs": json.dumps({}),
        "runnable_config": json.dumps({}),
        "tags": json.dumps([]),
        "callbacks": json.dumps([]),
        "metadata": json.dumps({}),
        "run_name": None,
        "status": "completed",
        "started_at": now,  # datetime object
        "completed_at": now,
        "error": None,
        "trace_id": "trace-123",
        "trace_started_at": now,  # datetime object
        "trace_completed_at": now,
        "trace_last_span_at": now,
    }

    record = PostgresRunHistoryStore._row_to_record(row, [])

    assert isinstance(record.trace_started_at, datetime)
    assert isinstance(record.trace_completed_at, datetime)
    assert isinstance(record.trace_last_span_at, datetime)


@pytest.mark.asyncio
async def test_postgres_store_row_to_record_parse_json_with_non_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _row_to_record parse_json handles non-string values."""
    now = datetime.now(tz=UTC)
    row = {
        "execution_id": "exec-456",
        "workflow_id": "wf-123",
        "inputs": {"key": "value"},  # Already a dict, not a string
        "runnable_config": {"config": "data"},
        "tags": ["tag1"],
        "callbacks": ["cb1"],
        "metadata": {"meta": "data"},
        "run_name": None,
        "status": "completed",
        "started_at": now,
        "completed_at": now,
        "error": None,
        "trace_id": None,
        "trace_started_at": None,
        "trace_completed_at": None,
        "trace_last_span_at": None,
    }

    record = PostgresRunHistoryStore._row_to_record(row, [])

    assert record.inputs == {"key": "value"}
    assert record.runnable_config == {"config": "data"}
    assert record.tags == ["tag1"]


@pytest.mark.asyncio
async def test_postgres_store_ensure_initialized_rollback_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that _ensure_initialized rolls back and re-raises on schema error."""

    class ErrorConnection(FakeConnection):
        async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
            raise RuntimeError("Schema creation failed")

    monkeypatch.setattr(pg_store, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_store, "DictRowFactory", object())
    store = PostgresRunHistoryStore("postgresql://test")
    error_conn = ErrorConnection([])
    store._pool = FakePool(error_conn)
    store._initialized = False

    with pytest.raises(RuntimeError, match="Schema creation failed"):
        await store._ensure_initialized()

    assert error_conn.rollbacks == 1
    assert error_conn.commits == 0
    assert store._initialized is False
