"""Tests for SQLite history utility helpers."""

from __future__ import annotations
from pathlib import Path
import aiosqlite
import pytest
from orcheo_backend.app.history import sqlite_utils


@pytest.mark.asyncio
async def test_ensure_trace_columns_upgrades_existing_table(tmp_path: Path) -> None:
    """_ensure_trace_columns should add missing trace columns during upgrades."""

    db_path = tmp_path / "history-upgrade.sqlite"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE execution_history (
                execution_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                inputs TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT
            )
            """
        )
        await sqlite_utils._ensure_trace_columns(conn)
        cursor = await conn.execute("PRAGMA table_info(execution_history)")
        rows = await cursor.fetchall()

    column_names = {row["name"] for row in rows}

    assert {
        "trace_id",
        "trace_started_at",
        "trace_completed_at",
        "trace_last_span_at",
    }.issubset(column_names)


@pytest.mark.asyncio
async def test_ensure_agentensor_workspace_column_adds_missing_column(
    tmp_path: Path,
) -> None:
    """_ensure_agentensor_workspace_column should add workspace_id when absent."""

    db_path = tmp_path / "history-agentensor-upgrade.sqlite"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE agentensor_checkpoints (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL
            )
            """
        )
        await sqlite_utils._ensure_agentensor_workspace_column(conn)
        cursor = await conn.execute("PRAGMA table_info(agentensor_checkpoints)")
        rows = await cursor.fetchall()

    column_names = {row["name"] for row in rows}
    assert "workspace_id" in column_names
