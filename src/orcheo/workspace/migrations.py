"""Helpers used during the multi-workspace backfill migration.

These utilities are deliberately conservative: they perform additive,
idempotent schema work and can create the legacy default workspace only for
backfill and compatibility purposes. They do **not** yet rewrite every existing
repository/store — that sweep is Milestone 2.
"""

from __future__ import annotations
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from orcheo.workspace.models import DEFAULT_WORKSPACE_SLUG, Workspace
from orcheo.workspace.repository import WorkspaceRepository
from orcheo.workspace.service import ensure_default_workspace
from orcheo.workspace.sqlite_store import (
    SQLITE_WORKSPACE_SCHEMA_SQL,
    ensure_workspace_schema,
)


__all__ = [
    "WORKSPACE_ID_BACKFILL_TABLES",
    "ensure_default_workspace_for_repository",
    "add_workspace_id_column_sqlite",
    "backfill_workspace_id_sqlite",
    "ensure_workspace_index_sqlite",
    "run_sqlite_backfill",
]


WORKSPACE_ID_BACKFILL_TABLES: tuple[str, ...] = (
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "execution_history",
    "execution_history_steps",
    "service_tokens",
    "service_token_audit_log",
    "credentials",
    "credential_templates",
    "governance_alerts",
    "chat_threads",
    "chat_messages",
    "chat_attachments",
    "agentensor_checkpoints",
    "plugin_installations",
    "listener_subscriptions",
    "listener_cursors",
    "listener_dedupe",
    "webhook_triggers",
    "cron_triggers",
    "retry_policies",
)


def ensure_default_workspace_for_repository(
    repository: WorkspaceRepository,
    *,
    slug: str = DEFAULT_WORKSPACE_SLUG,
    name: str = "Default Workspace",
) -> Workspace:
    """Create or fetch the legacy default workspace via the repository."""
    return ensure_default_workspace(repository, slug=slug, name=name)


def _table_exists_sqlite(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists_sqlite(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists_sqlite(conn, table):
        return False
    cursor = conn.execute(f'PRAGMA table_info("{table}")')
    for row in cursor.fetchall():
        # row layout: cid, name, type, notnull, dflt_value, pk
        if row[1] == column:
            return True
    return False


def add_workspace_id_column_sqlite(
    conn: sqlite3.Connection, table: str, *, column: str = "workspace_id"
) -> bool:
    """Add a nullable `workspace_id TEXT` column to `table` if missing.

    Returns True if a column was added, False otherwise.
    """
    if not _table_exists_sqlite(conn, table):
        return False
    if _column_exists_sqlite(conn, table, column):
        return False
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} TEXT')
    return True


def backfill_workspace_id_sqlite(
    conn: sqlite3.Connection,
    table: str,
    workspace_id: str,
    *,
    column: str = "workspace_id",
) -> int:
    """Set `workspace_id` to the given value for rows where it is NULL.

    Returns the number of rows updated.
    """
    if not _column_exists_sqlite(conn, table, column):
        return 0
    cursor = conn.execute(
        f'UPDATE "{table}" SET {column} = ? WHERE {column} IS NULL',
        (workspace_id,),
    )
    return cursor.rowcount


def ensure_workspace_index_sqlite(
    conn: sqlite3.Connection, table: str, *, column: str = "workspace_id"
) -> bool:
    """Create a basic index on the workspace column to support hot-path lookups."""
    if not _column_exists_sqlite(conn, table, column):
        return False
    index_name = f"idx_{table}_{column}"
    conn.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table}"({column})')
    return True


def run_sqlite_backfill(
    db_path: str | Path,
    workspace_id: str,
    *,
    tables: Iterable[str] = WORKSPACE_ID_BACKFILL_TABLES,
) -> dict[str, int]:
    """Apply the additive workspace migration to a SQLite database.

    Steps per table:
    1. Add nullable `workspace_id` column when missing.
    2. Backfill NULLs with the given workspace id.
    3. Create an index on the column.

    Returns a mapping of `table -> rows_backfilled` for tables that exist.
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with sqlite3.connect(path) as conn:
        # First make sure the workspace tables themselves are in place if the
        # caller is using this DB as the workspace store too.
        conn.executescript(SQLITE_WORKSPACE_SCHEMA_SQL)
        for table in tables:
            if not _table_exists_sqlite(conn, table):
                continue
            add_workspace_id_column_sqlite(conn, table)
            counts[table] = backfill_workspace_id_sqlite(conn, table, workspace_id)
            ensure_workspace_index_sqlite(conn, table)
    # Make sure the parent dir of the workspace store also has its schema.
    ensure_workspace_schema(path)
    return counts
