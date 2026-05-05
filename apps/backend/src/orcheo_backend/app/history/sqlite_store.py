"""SQLite-backed history store shared across backend workers."""

from __future__ import annotations
import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
import aiosqlite
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
from orcheo_backend.app.history.sqlite_utils import (
    INSERT_EXECUTION_SQL,
    INSERT_EXECUTION_STEP_SQL,
    LIST_HISTORIES_SQL,
    SELECT_CURRENT_STEP_INDEX_SQL,
    UPDATE_HISTORY_STATUS_SQL,
    UPDATE_TRACE_LAST_SPAN_SQL,
    connect_sqlite,
    ensure_sqlite_schema,
    fetch_record_row,
    fetch_steps,
    row_to_record,
)


class SqliteRunHistoryStore:
    """SQLite-backed store for execution histories shared across processes."""

    def __init__(self, database_path: str | Path) -> None:
        """Initialise the persistent history store."""
        self._database_path = Path(database_path).expanduser()
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

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
            config_payload_data = runnable_config
            if runnable_config and hasattr(runnable_config, "model_dump"):
                config_payload_data = runnable_config.model_dump(mode="json")  # type: ignore[arg-type]
            config_dict = normalize_json_mapping(
                config_payload_data
                if isinstance(config_payload_data, Mapping)
                else None
            )
            tag_values = (
                [str(tag) for tag in (tags or config_dict.get("tags", []))]
                if isinstance(config_payload_data, Mapping)
                else [str(tag) for tag in (tags or [])]
            )
            callback_values = (
                normalize_json_value(
                    list(callbacks or config_dict.get("callbacks", []))
                )
                if isinstance(config_payload_data, Mapping)
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
                if isinstance(config_payload_data, Mapping)
                else normalize_json_mapping(metadata)
            )
            run_identifier = run_name or (
                config_dict.get("run_name")
                if isinstance(config_dict, Mapping)
                else None
            )
            if run_identifier is not None:
                run_identifier = str(run_identifier)
            payload = json.dumps(inputs_data)
            config_payload = json.dumps(config_dict)
            tags_payload = json.dumps(tag_values)
            callbacks_payload = json.dumps(callback_values)
            metadata_payload = json.dumps(metadata_values)
            async with connect_sqlite(self._database_path) as conn:
                try:
                    await conn.execute(
                        INSERT_EXECUTION_SQL,
                        (
                            execution_id,
                            workflow_id,
                            workspace_id,
                            payload,
                            config_payload,
                            tags_payload,
                            callbacks_payload,
                            metadata_payload,
                            run_identifier,
                            "running",
                            started_at.isoformat(),
                            trace_id,
                            trace_started.isoformat() if trace_started else None,
                            None,
                            trace_started.isoformat() if trace_started else None,
                        ),
                    )
                    await conn.commit()
                except aiosqlite.IntegrityError as exc:  # pragma: no cover - defensive
                    msg = f"History already exists for execution_id={execution_id}"
                    raise RunHistoryError(msg) from exc
            return RunHistoryRecord(
                workflow_id=workflow_id,
                execution_id=execution_id,
                workspace_id=workspace_id,
                inputs=json.loads(payload),
                runnable_config=json.loads(config_payload),
                tags=json.loads(tags_payload),
                callbacks=json.loads(callbacks_payload),
                metadata=json.loads(metadata_payload),
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
            async with connect_sqlite(self._database_path) as conn:
                record_row = await fetch_record_row(conn, execution_id)
                if record_row is None:
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)

                cursor = await conn.execute(
                    SELECT_CURRENT_STEP_INDEX_SQL,
                    (execution_id,),
                )
                row = await cursor.fetchone()
                next_index = (row["current_index"] if row else -1) + 1

                await conn.execute(
                    INSERT_EXECUTION_STEP_SQL,
                    (
                        execution_id,
                        next_index,
                        at.isoformat(),
                        json.dumps(normalize_json_mapping(payload)),
                    ),
                )
                await conn.execute(
                    UPDATE_TRACE_LAST_SPAN_SQL,
                    (
                        at.isoformat(),
                        execution_id,
                    ),
                )
                await conn.commit()
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
        async with connect_sqlite(self._database_path) as conn:
            record_row = await fetch_record_row(conn, execution_id)
            if record_row is None:
                msg = f"History not found for execution_id={execution_id}"
                raise RunHistoryNotFoundError(msg)
            steps = await fetch_steps(conn, execution_id)
            return row_to_record(record_row, steps)

    async def clear(self) -> None:
        """Clear all stored histories. Intended for testing only."""
        await self._ensure_initialized()
        async with self._lock:
            async with connect_sqlite(self._database_path) as conn:
                await conn.execute("DELETE FROM execution_history_steps")
                await conn.execute("DELETE FROM execution_history")
                await conn.commit()

    async def list_histories(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        workspace_id: str | None = None,
    ) -> list[RunHistoryRecord]:
        """Return histories associated with the provided workflow."""
        await self._ensure_initialized()
        query = LIST_HISTORIES_SQL
        params: list[object] = [workflow_id]
        if workspace_id is not None:
            query += " AND (workspace_id = ? OR workspace_id IS NULL)"
            params.append(workspace_id)
        query += " ORDER BY started_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with connect_sqlite(self._database_path) as conn:
            cursor = await conn.execute(query, tuple(params))
            rows = await cursor.fetchall()
            records: list[RunHistoryRecord] = []
            for row in rows:
                steps = await fetch_steps(conn, row["execution_id"])
                records.append(row_to_record(row, steps))
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
            async with connect_sqlite(self._database_path) as conn:
                cursor = await conn.execute(
                    UPDATE_HISTORY_STATUS_SQL,
                    (
                        status,
                        completed_at.isoformat(),
                        error,
                        completed_at.isoformat(),
                        completed_at.isoformat(),
                        execution_id,
                    ),
                )
                if cursor.rowcount == 0:
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)
                await conn.commit()
                record_row = await fetch_record_row(conn, execution_id)
                if record_row is None:  # pragma: no cover - defensive
                    msg = f"History not found for execution_id={execution_id}"
                    raise RunHistoryNotFoundError(msg)
                steps = await fetch_steps(conn, execution_id)
        record = row_to_record(record_row, steps)
        record.completed_at = completed_at
        record.status = status
        record.error = error
        record.trace_completed_at = completed_at
        if record.trace_last_span_at is None:
            record.trace_last_span_at = completed_at
        return record

    async def _ensure_initialized(self) -> None:
        """Create the required tables if they are missing."""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            await ensure_sqlite_schema(self._database_path)
            self._initialized = True


__all__ = ["SqliteRunHistoryStore"]
