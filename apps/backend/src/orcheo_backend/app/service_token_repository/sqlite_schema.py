"""Schema helpers for the SQLite service token repository."""

from __future__ import annotations
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS service_tokens (
    identifier TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    scopes TEXT,
    workspace_ids TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    issued_at TEXT,
    expires_at TEXT,
    last_used_at TEXT,
    use_count INTEGER DEFAULT 0,
    rotation_expires_at TEXT,
    rotated_to TEXT,
    rotated_from TEXT,
    revoked_at TEXT,
    revoked_by TEXT,
    revocation_reason TEXT,
    allowed_ip_ranges TEXT,
    rate_limit_override INTEGER,
    tenant_id TEXT,
    FOREIGN KEY (rotated_to) REFERENCES service_tokens(identifier)
);

CREATE INDEX IF NOT EXISTS idx_service_tokens_hash
    ON service_tokens(secret_hash);
CREATE INDEX IF NOT EXISTS idx_service_tokens_expires
    ON service_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_service_tokens_active
    ON service_tokens(revoked_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS service_token_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    ip_address TEXT,
    user_agent TEXT,
    timestamp TEXT NOT NULL,
    details TEXT,
    tenant_id TEXT,
    FOREIGN KEY (token_id) REFERENCES service_tokens(identifier)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_token
    ON service_token_audit_log(token_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON service_token_audit_log(timestamp);
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f'PRAGMA table_info("{table}")')
    for row in cursor.fetchall():
        if row[1] == column:
            return True
    return False


def _ensure_tenant_id_column(conn: sqlite3.Connection, table: str) -> None:
    """Idempotent additive migration for the nullable tenant_id column."""
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    if cursor.fetchone() is None:
        return
    if not _column_exists(conn, table, "tenant_id"):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN tenant_id TEXT')


def ensure_schema(db_path: Path) -> None:
    """Create the required tables and indexes if they are missing."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # Existing databases predate the tenant_id column; add it if missing.
        _ensure_tenant_id_column(conn, "service_tokens")
        _ensure_tenant_id_column(conn, "service_token_audit_log")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant "
            "ON service_tokens(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant_identifier "
            "ON service_tokens(tenant_id, identifier)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant_hash "
            "ON service_tokens(tenant_id, secret_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_tenant "
            "ON service_token_audit_log(tenant_id)"
        )
