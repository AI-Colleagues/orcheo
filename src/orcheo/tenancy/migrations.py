"""Helpers used during the multi-tenancy backfill migration.

These utilities are deliberately conservative: they perform additive,
idempotent schema work and ensure the default tenant exists. They do **not**
yet rewrite every existing repository/store — that sweep is Milestone 2.
"""

from __future__ import annotations
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from orcheo.tenancy.models import DEFAULT_TENANT_SLUG, Tenant
from orcheo.tenancy.repository import TenantRepository
from orcheo.tenancy.service import ensure_default_tenant
from orcheo.tenancy.sqlite_store import (
    SQLITE_TENANT_SCHEMA_SQL,
    ensure_tenant_schema,
)


__all__ = [
    "TENANT_ID_BACKFILL_TABLES",
    "ensure_default_tenant_for_repository",
    "add_tenant_id_column_sqlite",
    "backfill_tenant_id_sqlite",
    "ensure_tenant_index_sqlite",
    "run_sqlite_backfill",
]


TENANT_ID_BACKFILL_TABLES: tuple[str, ...] = (
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


def ensure_default_tenant_for_repository(
    repository: TenantRepository,
    *,
    slug: str = DEFAULT_TENANT_SLUG,
    name: str = "Default Tenant",
) -> Tenant:
    """Create or fetch the default tenant via the supplied repository."""
    return ensure_default_tenant(repository, slug=slug, name=name)


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


def add_tenant_id_column_sqlite(
    conn: sqlite3.Connection, table: str, *, column: str = "tenant_id"
) -> bool:
    """Add a nullable `tenant_id TEXT` column to `table` if missing.

    Returns True if a column was added, False otherwise.
    """
    if not _table_exists_sqlite(conn, table):
        return False
    if _column_exists_sqlite(conn, table, column):
        return False
    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} TEXT')
    return True


def backfill_tenant_id_sqlite(
    conn: sqlite3.Connection,
    table: str,
    tenant_id: str,
    *,
    column: str = "tenant_id",
) -> int:
    """Set `tenant_id` to the given value for rows where it is NULL.

    Returns the number of rows updated.
    """
    if not _column_exists_sqlite(conn, table, column):
        return 0
    cursor = conn.execute(
        f'UPDATE "{table}" SET {column} = ? WHERE {column} IS NULL',
        (tenant_id,),
    )
    return cursor.rowcount


def ensure_tenant_index_sqlite(
    conn: sqlite3.Connection, table: str, *, column: str = "tenant_id"
) -> bool:
    """Create a basic index on the tenant column to support hot-path lookups."""
    if not _column_exists_sqlite(conn, table, column):
        return False
    index_name = f"idx_{table}_{column}"
    conn.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table}"({column})')
    return True


def run_sqlite_backfill(
    db_path: str | Path,
    tenant_id: str,
    *,
    tables: Iterable[str] = TENANT_ID_BACKFILL_TABLES,
) -> dict[str, int]:
    """Apply the additive tenancy migration to a SQLite database.

    Steps per table:
    1. Add nullable `tenant_id` column when missing.
    2. Backfill NULLs with the given tenant id.
    3. Create an index on the column.

    Returns a mapping of `table -> rows_backfilled` for tables that exist.
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with sqlite3.connect(path) as conn:
        # First make sure the tenant tables themselves are in place if the
        # caller is using this DB as the tenant store too.
        conn.executescript(SQLITE_TENANT_SCHEMA_SQL)
        for table in tables:
            if not _table_exists_sqlite(conn, table):
                continue
            add_tenant_id_column_sqlite(conn, table)
            counts[table] = backfill_tenant_id_sqlite(conn, table, tenant_id)
            ensure_tenant_index_sqlite(conn, table)
    # Make sure the parent dir of the tenant store also has its schema.
    ensure_tenant_schema(path)
    return counts
