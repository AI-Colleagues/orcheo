"""Tests for the additive multi-workspace SQLite backfill helper."""

from __future__ import annotations
import sqlite3
from pathlib import Path
from orcheo.workspace import (
    SqliteWorkspaceRepository,
    add_workspace_id_column_sqlite,
    backfill_workspace_id_sqlite,
    ensure_default_workspace,
    ensure_workspace_index_sqlite,
    run_sqlite_backfill,
)


def _seed_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE workflows (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE workflow_runs (id TEXT PRIMARY KEY, workflow_id TEXT)"
        )
        conn.execute("INSERT INTO workflows VALUES ('a', 'one')")
        conn.execute("INSERT INTO workflows VALUES ('b', 'two')")
        conn.execute("INSERT INTO workflow_runs VALUES ('r1', 'a')")


def test_run_sqlite_backfill_creates_columns_and_assigns_default(
    tmp_path: Path,
) -> None:
    db = tmp_path / "workflows.sqlite"
    _seed_db(db)
    repo = SqliteWorkspaceRepository(db)
    workspace = ensure_default_workspace(repo)
    counts = run_sqlite_backfill(db, str(workspace.id))
    assert counts == {"workflows": 2, "workflow_runs": 1}

    with sqlite3.connect(db) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(workflows)")]
        assert "workspace_id" in cols
        rows = list(conn.execute("SELECT id, workspace_id FROM workflows"))
        assert all(row[1] == str(workspace.id) for row in rows)
        # Index exists
        index_names = {
            row[1] for row in conn.execute("PRAGMA index_list('workflows')").fetchall()
        }
        assert "idx_workflows_workspace_id" in index_names


def test_run_sqlite_backfill_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "workflows.sqlite"
    _seed_db(db)
    repo = SqliteWorkspaceRepository(db)
    workspace = ensure_default_workspace(repo)
    run_sqlite_backfill(db, str(workspace.id))
    counts = run_sqlite_backfill(db, str(workspace.id))
    # No new rows to backfill, but column add should not fail.
    assert counts.get("workflows", 0) == 0
    assert counts.get("workflow_runs", 0) == 0


def test_helpers_handle_missing_tables(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    db.touch()
    with sqlite3.connect(db) as conn:
        assert add_workspace_id_column_sqlite(conn, "missing") is False
        assert backfill_workspace_id_sqlite(conn, "missing", "x") == 0
        assert ensure_workspace_index_sqlite(conn, "missing") is False


def test_run_sqlite_backfill_no_db_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "ghost.sqlite"
    counts = run_sqlite_backfill(db, "workspace-1")
    assert counts == {}
