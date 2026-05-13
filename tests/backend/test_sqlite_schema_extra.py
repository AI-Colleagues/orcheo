from __future__ import annotations
import sqlite3
from pathlib import Path
from orcheo_backend.app.service_token_repository import sqlite_schema


def test_ensure_workspace_id_column_skips_missing_table(tmp_path: Path) -> None:
    """Missing tables should be ignored by the migration helper."""

    db_path = tmp_path / "service-tokens.sqlite"
    with sqlite3.connect(db_path) as conn:
        sqlite_schema._ensure_workspace_id_column(conn, "service_tokens")

    assert db_path.exists()


def test_ensure_workspace_id_column_adds_missing_column(tmp_path: Path) -> None:
    """Existing tables without workspace_id should be altered in place."""

    db_path = tmp_path / "service-tokens.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE service_tokens (identifier TEXT PRIMARY KEY)")
        sqlite_schema._ensure_workspace_id_column(conn, "service_tokens")
        rows = conn.execute("PRAGMA table_info(service_tokens)").fetchall()

    column_names = {row[1] for row in rows}
    assert "workspace_id" in column_names


def test_ensure_schema_adds_workspace_indexes(tmp_path: Path) -> None:
    """ensure_schema should create the new workspace-aware indexes."""

    db_path = tmp_path / "service-tokens.sqlite"
    sqlite_schema.ensure_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(service_tokens)").fetchall()
        }

    assert "idx_service_tokens_workspace_id" in indexes
