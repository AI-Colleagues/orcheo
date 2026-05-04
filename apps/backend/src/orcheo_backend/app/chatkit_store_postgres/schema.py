"""Schema helpers for the PostgreSQL ChatKit store."""

from __future__ import annotations
from typing import Any


POSTGRES_CHATKIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    workflow_id TEXT,
    tenant_id TEXT,
    status_json JSONB NOT NULL,
    metadata_json JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_threads_created
    ON chat_threads(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_threads_updated
    ON chat_threads(updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_threads_workflow
    ON chat_threads(workflow_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_tenant
    ON chat_threads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_metadata
    ON chat_threads USING GIN (metadata_json);
CREATE INDEX IF NOT EXISTS idx_chat_threads_status
    ON chat_threads USING GIN (status_json);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    item_type TEXT,
    item_json JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages(thread_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created
    ON chat_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_item_type
    ON chat_messages(item_type);
CREATE INDEX IF NOT EXISTS idx_chat_messages_json
    ON chat_messages USING GIN (item_json);
CREATE INDEX IF NOT EXISTS idx_chat_messages_fts
    ON chat_messages USING GIN (to_tsvector('english', item_json::text));

CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    thread_id TEXT,
    attachment_type TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    details_json JSONB NOT NULL,
    storage_path TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
        ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_thread
    ON chat_attachments(thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_created
    ON chat_attachments(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_details
    ON chat_attachments USING GIN (details_json);
"""


async def ensure_schema(conn: Any) -> None:
    """Ensure tables and indexes exist."""
    for raw_stmt in POSTGRES_CHATKIT_SCHEMA.strip().split(";"):
        stmt = raw_stmt.strip()
        if stmt:
            await conn.execute(stmt)


__all__ = ["POSTGRES_CHATKIT_SCHEMA", "ensure_schema"]
