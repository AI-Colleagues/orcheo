"""Thread-level operations for the PostgreSQL ChatKit store."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from chatkit.store import NotFoundError
from chatkit.types import Page, ThreadMetadata
from orcheo_backend.app.chatkit_store_postgres.base import BasePostgresStore
from orcheo_backend.app.chatkit_store_postgres.serialization import (
    serialize_thread_status,
    thread_from_row,
)
from orcheo_backend.app.chatkit_store_postgres.types import ChatKitRequestContext
from orcheo_backend.app.chatkit_store_postgres.utils import (
    compact_json,
    ensure_datetime,
    now_utc,
)


def _extract_title_from_request(context: ChatKitRequestContext | None) -> str | None:
    """Return first 20 chars of the first user text content in the request."""
    if not context:
        return None
    request = context.get("chatkit_request")
    if request is None:
        return None
    params = getattr(request, "params", None)
    user_input = getattr(params, "input", None)
    for item in getattr(user_input, "content", []):
        text = getattr(item, "text", None)
        if text:
            return text[:20].strip() or None
    return None


class ThreadStoreMixin(BasePostgresStore):
    """CRUD helpers for thread metadata."""

    async def load_thread(
        self, thread_id: str, context: ChatKitRequestContext
    ) -> ThreadMetadata:
        """Return metadata for ``thread_id``."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, title, status_json, metadata_json, created_at
                  FROM chat_threads
                 WHERE id = %s
                """,
                (thread_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError(f"Thread {thread_id} not found")
        return thread_from_row(row)

    async def save_thread(
        self, thread: ThreadMetadata, context: ChatKitRequestContext
    ) -> None:
        """Insert or update metadata for ``thread``."""
        await self._ensure_initialized()
        if not thread.title:
            thread.title = _extract_title_from_request(context)
        async with self._lock:
            async with self._connection() as conn:
                metadata_payload = self._merge_metadata_from_context(thread, context)
                workflow_id = metadata_payload.get("workflow_id")
                await conn.execute(
                    """
                    INSERT INTO chat_threads (
                        id,
                        title,
                        workflow_id,
                        status_json,
                        metadata_json,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        workflow_id = excluded.workflow_id,
                        status_json = excluded.status_json,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        thread.id,
                        thread.title,
                        str(workflow_id) if workflow_id else None,
                        serialize_thread_status(thread),
                        compact_json(metadata_payload),
                        ensure_datetime(thread.created_at),
                        now_utc(),
                    ),
                )

    async def load_threads(
        self,
        limit: int,
        after: str | None,
        order: str,
        context: ChatKitRequestContext,
    ) -> Page[ThreadMetadata]:
        """Return a paginated collection of threads scoped to the workflow."""
        await self._ensure_initialized()
        workflow_id: str | None = context.get("workflow_id") if context else None
        limit = max(limit, 1)
        ordering = "asc" if order.lower() == "asc" else "desc"
        comparator = ">" if ordering == "asc" else "<"
        params: list[Any] = []
        conditions: list[str] = []

        if workflow_id:
            conditions.append("workflow_id = %s")
            params.append(workflow_id)

        async with self._connection() as conn:
            if after:  # pragma: no branch
                # Cursor lookup must be scoped to the same workflow to prevent
                # information leakage and ensure consistent pagination
                cursor_query = "SELECT created_at, id FROM chat_threads WHERE id = %s"
                cursor_params = [after]
                if workflow_id:
                    cursor_query += " AND workflow_id = %s"
                    cursor_params.append(workflow_id)

                cursor = await conn.execute(cursor_query, tuple(cursor_params))
                marker = await cursor.fetchone()
                if marker is not None:
                    created_at = marker["created_at"]
                    conditions.append(f"((created_at, id) {comparator} (%s, %s))")
                    params.extend([created_at, marker["id"]])

            query = (
                "SELECT id, title, status_json, metadata_json, created_at "
                "FROM chat_threads"
            )
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += f" ORDER BY created_at {ordering.upper()}, id {ordering.upper()}"
            query += " LIMIT %s"
            params.append(limit + 1)

            cursor = await conn.execute(query, tuple(params))
            rows = list(await cursor.fetchall())

        has_more = len(rows) > limit
        sliced = rows[:limit]
        threads = [thread_from_row(row) for row in sliced]
        next_after = threads[-1].id if has_more and threads else None
        return Page(data=threads, has_more=has_more, after=next_after)

    async def delete_thread(
        self, thread_id: str, context: ChatKitRequestContext
    ) -> None:
        """Remove ``thread_id`` and cascade associated entities."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    "DELETE FROM chat_threads WHERE id = %s",
                    (thread_id,),
                )

    async def filter_threads(
        self,
        metadata_filter: Mapping[str, Any],
        *,
        limit: int = 50,
        after: str | None = None,
        order: str = "desc",
    ) -> Page[ThreadMetadata]:
        """Return threads matching a JSONB metadata filter."""
        await self._ensure_initialized()
        limit = max(limit, 1)
        ordering = "asc" if order.lower() == "asc" else "desc"
        comparator = ">" if ordering == "asc" else "<"
        params: list[Any] = [compact_json(metadata_filter)]
        where_clause = " WHERE metadata_json @> %s"

        async with self._connection() as conn:
            if after:
                cursor = await conn.execute(
                    "SELECT created_at, id FROM chat_threads WHERE id = %s",
                    (after,),
                )
                marker = await cursor.fetchone()
                if marker is not None:
                    where_clause += f" AND (created_at, id) {comparator} (%s, %s)"
                    params.extend([marker["created_at"], marker["id"]])

            query = (
                "SELECT id, title, status_json, metadata_json, created_at "
                "FROM chat_threads"
            )
            query += where_clause
            query += f" ORDER BY created_at {ordering.upper()}, id {ordering.upper()}"
            query += " LIMIT %s"
            params.append(limit + 1)

            cursor = await conn.execute(query, tuple(params))
            rows = list(await cursor.fetchall())

        has_more = len(rows) > limit
        sliced = rows[:limit]
        threads = [thread_from_row(row) for row in sliced]
        next_after = threads[-1].id if has_more and threads else None
        return Page(data=threads, has_more=has_more, after=next_after)

    @staticmethod
    def _merge_metadata_from_context(
        thread: ThreadMetadata, context: ChatKitRequestContext | None
    ) -> dict[str, Any]:
        existing = dict(thread.metadata or {})
        if not context:
            thread.metadata = existing
            return existing

        request = context.get("chatkit_request")
        metadata = getattr(request, "metadata", None)
        if isinstance(metadata, dict) and metadata:
            merged = {**existing, **metadata}
            thread.metadata = merged
            return merged

        thread.metadata = existing
        return existing


__all__ = ["ThreadStoreMixin"]
