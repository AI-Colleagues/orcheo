"""Shared helpers for the SQLite run history store."""

from __future__ import annotations
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import aiosqlite
from orcheo_backend.app.history.models import RunHistoryRecord, RunHistoryStep


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS execution_history (
    execution_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workspace_id TEXT,
    inputs TEXT NOT NULL,
    runnable_config TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '[]',
    callbacks TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    run_name TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    trace_id TEXT,
    trace_started_at TEXT,
    trace_completed_at TEXT,
    trace_last_span_at TEXT
);
CREATE TABLE IF NOT EXISTS execution_history_steps (
    execution_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (execution_id, step_index),
    FOREIGN KEY (execution_id)
        REFERENCES execution_history(execution_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_history_steps_execution
    ON execution_history_steps(execution_id, step_index);
CREATE TABLE IF NOT EXISTS agentensor_checkpoints (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workspace_id TEXT,
    config_version INTEGER NOT NULL,
    runnable_config TEXT NOT NULL,
    metrics TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    artifact_url TEXT,
    is_best INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_workflow
    ON agentensor_checkpoints(workflow_id, config_version);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_best
    ON agentensor_checkpoints(workflow_id, is_best);
CREATE INDEX IF NOT EXISTS idx_agentensor_checkpoints_workspace_id
    ON agentensor_checkpoints(workspace_id, workflow_id, config_version);
CREATE TABLE IF NOT EXISTS plugin_installations (
    plugin_name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (plugin_name, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_plugin_installations_workspace_id
    ON plugin_installations(workspace_id);
"""

INSERT_EXECUTION_SQL = """
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
    completed_at,
    error,
    trace_id,
    trace_started_at,
    trace_completed_at,
    trace_last_span_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
"""

SELECT_CURRENT_STEP_INDEX_SQL = """
SELECT COALESCE(MAX(step_index), -1) AS current_index
  FROM execution_history_steps
 WHERE execution_id = ?
"""

INSERT_EXECUTION_STEP_SQL = """
INSERT INTO execution_history_steps (
    execution_id,
    step_index,
    at,
    payload
)
VALUES (?, ?, ?, ?)
"""

LIST_HISTORIES_SQL = (
    "SELECT execution_id, workflow_id, workspace_id, inputs, runnable_config, "
    "tags, callbacks, "
    "metadata, run_name, status, started_at, completed_at, "
    "error, trace_id, trace_started_at, trace_completed_at, trace_last_span_at "
    "FROM execution_history WHERE workflow_id = ?"
)

UPDATE_HISTORY_STATUS_SQL = """
UPDATE execution_history
   SET status = ?,
       completed_at = ?,
       error = ?,
       trace_completed_at = ?,
       trace_last_span_at = COALESCE(trace_last_span_at, ?)
 WHERE execution_id = ?
"""

UPDATE_TRACE_LAST_SPAN_SQL = """
UPDATE execution_history
   SET trace_last_span_at = ?
 WHERE execution_id = ?
"""

_TRACE_COLUMN_ALTERS: dict[str, str] = {
    "trace_id": "ALTER TABLE execution_history ADD COLUMN trace_id TEXT",
    "trace_started_at": (
        "ALTER TABLE execution_history ADD COLUMN trace_started_at TEXT"
    ),
    "trace_completed_at": (
        "ALTER TABLE execution_history ADD COLUMN trace_completed_at TEXT"
    ),
    "trace_last_span_at": (
        "ALTER TABLE execution_history ADD COLUMN trace_last_span_at TEXT"
    ),
}
_OPTIONAL_COLUMN_ALTERS: dict[str, str] = {
    "runnable_config": (
        "ALTER TABLE execution_history ADD COLUMN runnable_config TEXT DEFAULT '{}'"
    ),
    "tags": "ALTER TABLE execution_history ADD COLUMN tags TEXT DEFAULT '[]'",
    "callbacks": (
        "ALTER TABLE execution_history ADD COLUMN callbacks TEXT DEFAULT '[]'"
    ),
    "metadata": ("ALTER TABLE execution_history ADD COLUMN metadata TEXT DEFAULT '{}'"),
    "run_name": "ALTER TABLE execution_history ADD COLUMN run_name TEXT",
    "workspace_id": "ALTER TABLE execution_history ADD COLUMN workspace_id TEXT",
}


@asynccontextmanager
async def connect_sqlite(database_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Yield a configured SQLite connection for the history database."""
    conn = await aiosqlite.connect(database_path)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        await conn.close()


async def ensure_sqlite_schema(database_path: Path) -> None:
    """Create the history tables when the database is initialised."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    async with connect_sqlite(database_path) as conn:
        await _ensure_agentensor_workspace_column(conn)
        await conn.executescript(SCHEMA_SQL)
        await _ensure_trace_columns(conn)
        await conn.commit()


async def fetch_record_row(
    conn: aiosqlite.Connection,
    execution_id: str,
) -> aiosqlite.Row | None:
    """Return the raw execution history row if present."""
    cursor = await conn.execute(
        """
        SELECT execution_id, workflow_id, workspace_id, inputs, runnable_config,
               tags, callbacks, metadata, run_name, status, started_at,
               completed_at, error, trace_id, trace_started_at,
               trace_completed_at, trace_last_span_at
          FROM execution_history
         WHERE execution_id = ?
        """,
        (execution_id,),
    )
    return await cursor.fetchone()


async def fetch_steps(
    conn: aiosqlite.Connection,
    execution_id: str,
) -> list[RunHistoryStep]:
    """Return ordered steps for the provided execution."""
    cursor = await conn.execute(
        """
        SELECT step_index, at, payload
          FROM execution_history_steps
         WHERE execution_id = ?
         ORDER BY step_index ASC
        """,
        (execution_id,),
    )
    rows = await cursor.fetchall()
    steps: list[RunHistoryStep] = []
    for row in rows:
        steps.append(
            RunHistoryStep(
                index=row["step_index"],
                at=datetime.fromisoformat(row["at"]),
                payload=json.loads(row["payload"]),
            )
        )
    return steps


def row_to_record(
    row: aiosqlite.Row,
    steps: list[RunHistoryStep],
) -> RunHistoryRecord:
    """Convert a SQLite row into a RunHistoryRecord instance."""
    completed_at = (
        datetime.fromisoformat(row["completed_at"])
        if row["completed_at"] is not None
        else None
    )
    trace_started_at = _parse_optional_datetime(row["trace_started_at"])
    trace_completed_at = _parse_optional_datetime(row["trace_completed_at"])
    trace_last_span_at = _parse_optional_datetime(row["trace_last_span_at"])
    return RunHistoryRecord(
        workflow_id=row["workflow_id"],
        execution_id=row["execution_id"],
        workspace_id=row["workspace_id"],
        inputs=json.loads(row["inputs"]),
        runnable_config=json.loads(row["runnable_config"]),
        tags=json.loads(row["tags"]),
        callbacks=json.loads(row["callbacks"]),
        metadata=json.loads(row["metadata"]),
        run_name=row["run_name"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=completed_at,
        error=row["error"],
        steps=steps,
        trace_id=row["trace_id"],
        trace_started_at=trace_started_at,
        trace_completed_at=trace_completed_at,
        trace_last_span_at=trace_last_span_at,
    )


async def _ensure_trace_columns(conn: aiosqlite.Connection) -> None:
    """Add trace metadata columns when upgrading existing databases."""
    cursor = await conn.execute("PRAGMA table_info(execution_history)")
    rows = await cursor.fetchall()
    existing = {row["name"] for row in rows}
    alters = {**_TRACE_COLUMN_ALTERS, **_OPTIONAL_COLUMN_ALTERS}
    for column, statement in alters.items():
        if column not in existing:
            await conn.execute(statement)


async def _ensure_agentensor_workspace_column(conn: aiosqlite.Connection) -> None:
    """Add workspace_id to agentensor_checkpoints when upgrading existing databases."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='agentensor_checkpoints'"
    )
    if await cursor.fetchone() is None:
        return
    cursor = await conn.execute("PRAGMA table_info(agentensor_checkpoints)")
    existing = {row["name"] for row in await cursor.fetchall()}
    if "workspace_id" not in existing:
        await conn.execute(
            "ALTER TABLE agentensor_checkpoints ADD COLUMN workspace_id TEXT"
        )


def _parse_optional_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp if present."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


__all__ = [
    "INSERT_EXECUTION_SQL",
    "INSERT_EXECUTION_STEP_SQL",
    "LIST_HISTORIES_SQL",
    "SCHEMA_SQL",
    "SELECT_CURRENT_STEP_INDEX_SQL",
    "UPDATE_HISTORY_STATUS_SQL",
    "UPDATE_TRACE_LAST_SPAN_SQL",
    "connect_sqlite",
    "ensure_sqlite_schema",
    "fetch_record_row",
    "fetch_steps",
    "row_to_record",
]
