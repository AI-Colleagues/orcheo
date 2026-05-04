"""Schema creation and migrations for the SQLite ChatKit store."""

from __future__ import annotations
import json
import logging
import aiosqlite


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    workflow_id TEXT,
    tenant_id TEXT,
    status_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_threads_tenant
    ON chat_threads(tenant_id);
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    item_type TEXT,
    item_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages(thread_id, ordinal);
CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    thread_id TEXT,
    attachment_type TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    storage_path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
        ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_thread
    ON chat_attachments(thread_id);
"""

logger = logging.getLogger(__name__)


async def ensure_schema(conn: aiosqlite.Connection) -> None:
    """Ensure tables and indexes exist."""
    await run_migrations(conn)
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()


async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply pending migrations."""
    await _migrate_chat_messages_thread_id(conn)
    await _migrate_chat_threads_tenant_id(conn)


async def _migrate_chat_threads_tenant_id(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute(
        """
        SELECT name
          FROM sqlite_master
         WHERE type = 'table' AND name = 'chat_threads'
        """
    )
    table = await cursor.fetchone()
    if table is None:
        return
    cursor = await conn.execute("PRAGMA table_info(chat_threads)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "tenant_id" not in columns:
        await conn.execute("ALTER TABLE chat_threads ADD COLUMN tenant_id TEXT")
        await conn.commit()


async def _migrate_chat_messages_thread_id(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute(
        """
        SELECT name
          FROM sqlite_master
         WHERE type = 'table' AND name = 'chat_messages'
        """
    )
    table = await cursor.fetchone()
    if table is None:
        return

    cursor = await conn.execute("PRAGMA table_info(chat_messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "thread_id" in columns:
        return

    logger.info("Migrating chat_messages table to add thread_id column")
    cursor = await conn.execute(
        """
        SELECT id, ordinal, item_type, item_json, created_at
          FROM chat_messages
         ORDER BY ordinal
        """
    )
    rows = await cursor.fetchall()

    await conn.execute("PRAGMA foreign_keys = OFF;")
    await conn.execute("DROP TABLE IF EXISTS chat_messages__new;")
    await conn.execute("BEGIN;")
    try:
        await conn.execute(
            """
            CREATE TABLE chat_messages__new (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                item_type TEXT,
                item_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
                    ON DELETE CASCADE
            )
            """
        )

        for row in rows:
            payload = json.loads(row["item_json"])
            thread_id = (
                payload.get("thread_id")
                or payload.get("threadId")
                or payload.get("metadata", {}).get("thread_id")
            )
            if thread_id is None:
                logger.warning(
                    (
                        "Dropping message %s while migrating chat_messages; "
                        "missing thread_id"
                    ),
                    row["id"],
                )
                continue

            await conn.execute(
                """
                INSERT INTO chat_messages__new (
                    id,
                    thread_id,
                    ordinal,
                    item_type,
                    item_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    str(thread_id),
                    row["ordinal"],
                    row["item_type"],
                    row["item_json"],
                    row["created_at"],
                ),
            )

        await conn.execute("DROP TABLE chat_messages;")
        await conn.execute("ALTER TABLE chat_messages__new RENAME TO chat_messages;")
        await conn.commit()
        logger.info("chat_messages migration completed successfully")
    except Exception:
        await conn.rollback()
        logger.exception(
            "Failed to migrate chat_messages table to include thread_id column"
        )
        raise
    finally:
        await conn.execute("PRAGMA foreign_keys = ON;")


__all__ = ["ensure_schema", "run_migrations"]
