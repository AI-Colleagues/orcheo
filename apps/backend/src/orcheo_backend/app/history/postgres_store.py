"""PostgreSQL-backed history store for production deployments."""

from __future__ import annotations
import asyncio
import importlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from orcheo_backend.app.history.models import (
    RunHistoryError,
    RunHistoryNotFoundError,
    RunHistoryRecord,
    RunHistoryStep,
    _utcnow,
)
from orcheo_backend.app.history.serialization import (
    normalize_json_mapping,
    normalize_json_value,
)


# Optional psycopg dependencies
AsyncConnectionPool: Any | None
DictRowFactory: Any | None

try:  # pragma: no cover - optional dependency
    AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover - fallback when dependency missing
    AsyncConnectionPool = None
    DictRowFactory = None


POSTGRES_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_history (
    execution_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workspace_id TEXT,
    inputs JSONB NOT NULL,
    runnable_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    callbacks JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_name TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    error TEXT,
    trace_id TEXT,
    trace_started_at TIMESTAMP WITH TIME ZONE,
    trace_completed_at TIMESTAMP WITH TIME ZONE,
    trace_last_span_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS idx_execution_history_workflow
    ON execution_history(workflow_id);

CREATE TABLE IF NOT EXISTS execution_history_steps (
    execution_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    at TIMESTAMP WITH TIME ZONE NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (execution_id, step_index),
    FOREIGN KEY (execution_id)
        REFERENCES execution_history(execution_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_history_steps_execution
    ON execution_history_steps(execution_id, step_index);
"""

# Applied after POSTGRES_HISTORY_SCHEMA to bring existing databases up to date.
# ADD COLUMN IF NOT EXISTS is a no-op when the column already exists (new databases).
POSTGRES_HISTORY_MIGRATIONS = [
    "ALTER TABLE execution_history ADD COLUMN IF NOT EXISTS workspace_id TEXT",
    (
        "CREATE INDEX IF NOT EXISTS idx_execution_history_workspace_id "
        "ON execution_history(workspace_id)"
    ),
]


class PostgresRunHistoryStore:
    """PostgreSQL-backed store for execution histories."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Initialize the PostgreSQL history store."""
        if AsyncConnectionPool is None or DictRowFactory is None:
            msg = "PostgreSQL backend requires psycopg[binary,pool] to be installed."
            raise RuntimeError(msg)

        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_max_idle = pool_max_idle
        self._pool: Any | None = None
        self._lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        """Return the connection pool, creating it if necessary."""
        if self._pool is not None:
            return self._pool

        async with self._pool_lock:
            if self._pool is not None:
                return self._pool

            pool_class = AsyncConnectionPool
            assert pool_class is not None  # mypy
            self._pool = pool_class(
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                timeout=self._pool_timeout,
                max_idle=self._pool_max_idle,
                open=False,
                kwargs={
                    "autocommit": False,
                    "prepare_threshold": 0,
                    "row_factory": DictRowFactory,
                },
            )
            await self._pool.open()
            return self._pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._schema_lock:
            if self._initialized:
                return

            pool = await self._get_pool()
            async with pool.connection() as conn:
                try:
                    for raw_stmt in POSTGRES_HISTORY_SCHEMA.strip().split(";"):
                        stmt = raw_stmt.strip()
                        if stmt:
                            await conn.execute(stmt)
                    for stmt in POSTGRES_HISTORY_MIGRATIONS:
                        await conn.execute(stmt)
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

            self._initialized = True

    async def start_run(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        inputs: Mapping[str, Any] | None = None,
        runnable_config: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        callbacks: list[Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        run_name: str | None = None,
        trace_id: str | None = None,
        trace_started_at: datetime | None = None,
        workspace_id: str | None = None,
    ) -> RunHistoryRecord:
        """Initialise a history record for the provided execution."""
        await self._ensure_initialized()
        async with self._lock:
            started_at = _utcnow()
            trace_started = trace_started_at or started_at
            inputs_data = normalize_json_mapping(inputs)
            config_data = runnable_config
            if runnable_config and hasattr(runnable_config, "model_dump"):
                config_data = runnable_config.model_dump(mode="json")  # type: ignore[arg-type]
            config_dict = normalize_json_mapping(
                config_data if isinstance(config_data, Mapping) else None
            )
            tag_values = (
                [str(tag) for tag in (tags or config_dict.get("tags", []))]
                if isinstance(config_dict, Mapping)
                else [str(tag) for tag in (tags or [])]
            )
            callback_values = (
                normalize_json_value(
                    list(callbacks or config_dict.get("callbacks", []))
                )
                if isinstance(config_dict, Mapping)
                else normalize_json_value(list(callbacks or []))
            )
            if not isinstance(callback_values, list):
                callback_values = [callback_values]
            metadata_values = (
                normalize_json_mapping(
                    metadata
                    if metadata is not None
                    else config_dict.get("metadata", {})
                    if isinstance(config_dict.get("metadata", {}), Mapping)
                    else None
                )
                if isinstance(config_dict, Mapping)
                else normalize_json_mapping(metadata)
            )
            run_identifier = run_name or (
                config_dict.get("run_name")
                if isinstance(config_dict, Mapping)
                else None
            )
            if run_identifier is not None:
                run_identifier = str(run_identifier)
            async with self._connection() as conn:
                try:
                    await conn.execute(
                        """
                        INSERT INTO execution_history (
                            execution_id,
                            workflow_id,
                            workspace_id,
                            inputs,
                            runnable_config,
                            tags,
                            callbacks,
                            metadata,
                            run_name,
                            status,
                            started_at,
                            trace_id,
                            trace_started_at,
                            trace_last_span_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            execution_id,
                            workflow_id,
                            workspace_id,
                            json.dumps(inputs_data),
                            json.dumps(config_dict),
                            json.dumps(tag_values),
                            json.dumps(callback_values),
                            json.dumps(metadata_values),
                            run_identifier,
                            "running",
                            started_at,
                            trace_id,
                            trace_started,
                            trace_started,
                        ),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    if "duplicate key" in str(exc).lower():
                        msg = f"History already exists for execution_id={execution_id}"
                        raise RunHistoryError(msg) from exc
                    raise
            return RunHistoryRecord(
                workflow_id=workflow_id,
                execution_id=execution_id,
                workspace_id=workspace_id,
                inputs=inputs_data,
                runnable_config=config_dict,
                tags=tag_values,
                callbacks=callback_values,
                metadata=metadata_values,
                run_name=run_identifier,
                status="running",
                started_at=started_at,
                steps=[],
                trace_id=trace_id,
                trace_started_at=trace_started,
                trace_last_span_at=trace_started,
            )

    async def append_step(
        self,
        execution_id: str,
        payload: Mapping[str, Any],
    ) -> RunHistoryStep:
        """Append a step for the execution."""
        await self._ensure_initialized()
        async with self._lock:
            at = _utcnow()
            async with self._connection() as conn:
                record = await self._fetch_record(conn, execution_id)
                if record is None:
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)

                cursor = await conn.execute(
                    """
                    SELECT COALESCE(MAX(step_index), -1) AS current_index
                      FROM execution_history_steps
                     WHERE execution_id = %s
                    """,
                    (execution_id,),
                )
                row = await cursor.fetchone()
                next_index = (row["current_index"] if row else -1) + 1

                await conn.execute(
                    """
                    INSERT INTO execution_history_steps (
                        execution_id, step_index, at, payload
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        execution_id,
                        next_index,
                        at,
                        json.dumps(normalize_json_mapping(payload)),
                    ),
                )
                await conn.execute(
                    """
                    UPDATE execution_history
                       SET trace_last_span_at = %s
                     WHERE execution_id = %s
                    """,
                    (at, execution_id),
                )
        return RunHistoryStep(
            index=next_index,
            at=at,
            payload=normalize_json_mapping(payload),
        )

    async def mark_completed(self, execution_id: str) -> RunHistoryRecord:
        """Mark the execution as completed."""
        return await self._update_status(execution_id, status="completed", error=None)

    async def mark_failed(
        self,
        execution_id: str,
        error: str,
    ) -> RunHistoryRecord:
        """Mark the execution as failed with the specified error message."""
        return await self._update_status(execution_id, status="error", error=error)

    async def mark_cancelled(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> RunHistoryRecord:
        """Mark the execution as cancelled."""
        return await self._update_status(execution_id, status="cancelled", error=reason)

    async def get_history(self, execution_id: str) -> RunHistoryRecord:
        """Return a deep copy of the execution history."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            record = await self._fetch_record(conn, execution_id)
            if record is None:
                msg = f"History not found for execution_id={execution_id}"
                raise RunHistoryNotFoundError(msg)
            steps = await self._fetch_steps(conn, execution_id)
            return self._row_to_record(record, steps)

    async def clear(self) -> None:
        """Clear all stored histories. Intended for testing only."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute("DELETE FROM execution_history_steps")
                await conn.execute("DELETE FROM execution_history")

    async def list_histories(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        workspace_id: str | None = None,
    ) -> list[RunHistoryRecord]:
        """Return histories associated with the provided workflow."""
        await self._ensure_initialized()
        query = """
            SELECT execution_id, workflow_id, workspace_id, inputs,
                   runnable_config, tags, callbacks, metadata, run_name, status,
                   started_at, completed_at, error, trace_id, trace_started_at,
                   trace_completed_at, trace_last_span_at
              FROM execution_history
             WHERE workflow_id = %s
        """
        params: list[object] = [workflow_id]
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params.append(workspace_id)
        query += " ORDER BY started_at DESC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        async with self._connection() as conn:
            cursor = await conn.execute(query, tuple(params))
            rows = await cursor.fetchall()
            records: list[RunHistoryRecord] = []
            for row in rows:
                steps = await self._fetch_steps(conn, row["execution_id"])
                records.append(self._row_to_record(row, steps))
        return records

    async def _update_status(
        self,
        execution_id: str,
        *,
        status: str,
        error: str | None,
    ) -> RunHistoryRecord:
        """Persist the execution status mutation and return the updated record."""
        await self._ensure_initialized()
        async with self._lock:
            completed_at = _utcnow()
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE execution_history
                       SET status = %s,
                           completed_at = %s,
                           error = %s,
                           trace_completed_at = %s,
                           trace_last_span_at = COALESCE(trace_last_span_at, %s)
                     WHERE execution_id = %s
                    """,
                    (
                        status,
                        completed_at,
                        error,
                        completed_at,
                        completed_at,
                        execution_id,
                    ),
                )
                if cursor.rowcount == 0:
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)

                record = await self._fetch_record(conn, execution_id)
                if record is None:  # pragma: no cover - defensive
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)
                steps = await self._fetch_steps(conn, execution_id)
        result = self._row_to_record(record, steps)
        result.completed_at = completed_at
        result.status = status
        result.error = error
        result.trace_completed_at = completed_at
        if result.trace_last_span_at is None:
            result.trace_last_span_at = completed_at
        return result

    async def _fetch_record(
        self, conn: Any, execution_id: str
    ) -> dict[str, Any] | None:
        """Return the raw execution history row if present."""
        cursor = await conn.execute(
            """
            SELECT execution_id, workflow_id, workspace_id, inputs,
                   runnable_config, tags, callbacks, metadata, run_name, status,
                   started_at, completed_at, error, trace_id, trace_started_at,
                   trace_completed_at, trace_last_span_at
              FROM execution_history
             WHERE execution_id = %s
            """,
            (execution_id,),
        )
        return await cursor.fetchone()

    async def _fetch_steps(self, conn: Any, execution_id: str) -> list[RunHistoryStep]:
        """Return ordered steps for the provided execution."""
        cursor = await conn.execute(
            """
            SELECT step_index, at, payload
              FROM execution_history_steps
             WHERE execution_id = %s
             ORDER BY step_index ASC
            """,
            (execution_id,),
        )
        rows = await cursor.fetchall()
        steps: list[RunHistoryStep] = []
        for row in rows:
            at_value = row["at"]
            if isinstance(at_value, str):  # pragma: no branch
                at_value = datetime.fromisoformat(at_value)
            payload = row["payload"]
            if isinstance(payload, str):  # pragma: no branch
                payload = json.loads(payload)
            steps.append(
                RunHistoryStep(
                    index=row["step_index"],
                    at=at_value,
                    payload=payload,
                )
            )
        return steps

    @staticmethod
    def _row_to_record(
        row: dict[str, Any],
        steps: list[RunHistoryStep],
    ) -> RunHistoryRecord:
        """Convert a PostgreSQL row into a RunHistoryRecord instance."""
        completed_at = row["completed_at"]
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)

        started_at = row["started_at"]
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        def parse_ts(val: Any) -> datetime | None:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        def parse_json(val: Any) -> Any:
            if isinstance(val, str):
                return json.loads(val)
            return val

        return RunHistoryRecord(
            workflow_id=row["workflow_id"],
            execution_id=row["execution_id"],
            workspace_id=row.get("workspace_id"),
            inputs=parse_json(row["inputs"]),
            runnable_config=parse_json(row["runnable_config"]),
            tags=parse_json(row["tags"]),
            callbacks=parse_json(row["callbacks"]),
            metadata=parse_json(row["metadata"]),
            run_name=row["run_name"],
            status=row["status"],
            started_at=started_at,
            completed_at=completed_at,
            error=row["error"],
            steps=steps,
            trace_id=row["trace_id"],
            trace_started_at=parse_ts(row["trace_started_at"]),
            trace_completed_at=parse_ts(row["trace_completed_at"]),
            trace_last_span_at=parse_ts(row["trace_last_span_at"]),
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


__all__ = ["PostgresRunHistoryStore"]
