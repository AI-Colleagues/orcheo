"""Backend attachment service for ChatKit blob storage.

Owns all attachment persistence: metadata rows, blob payloads, scoped reads,
deletion, and upload-session-to-thread linking.  Workflow code calls the
``AttachmentResolver`` protocol in core; this module implements it.
"""

from __future__ import annotations
import asyncio
import dataclasses
import hashlib
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4
from orcheo.runtime.attachments import (
    AttachmentResolver,
    AttachmentScope,
    AttachmentUploader,
)
from orcheo_backend.app.chatkit_store_postgres.blob_backends import BlobBackend
from orcheo_backend.app.chatkit_store_postgres.utils import compact_json


logger = logging.getLogger(__name__)

_DEFAULT_BLOB_BACKEND = "postgres"
_ORPHAN_CUTOFF_HOURS = 24


@dataclasses.dataclass(slots=True)
class _AttachmentScopeImpl:
    """Concrete scope value built from trusted runtime context."""

    workspace_id: str
    workflow_id: str | None = None
    thread_id: str | None = None
    upload_session_id: str | None = None


@dataclasses.dataclass(slots=True)
class _AttachmentPayloadImpl:
    """Concrete resolved attachment payload."""

    id: str
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mint_attachment_id() -> str:
    return f"atc_{uuid4().hex}"


def _mint_upload_session_id() -> str:
    return f"ups_{uuid4().hex}"


class AttachmentNotFoundError(Exception):
    """Raised when an attachment cannot be found within the given scope."""


class AttachmentService:
    """Manages attachment metadata and blob payloads for ChatKit.

    Uses the connection pool from the ChatKit Postgres store.  Callers supply
    a *connection factory* — a zero-argument async context manager that yields
    a psycopg connection — matching the pattern used by ``BasePostgresStore``.
    """

    def __init__(
        self,
        connection_factory: Any,
        lock: asyncio.Lock,
        *,
        max_size_bytes: int = 10 * 1024 * 1024,
        orphan_cutoff_hours: int = _ORPHAN_CUTOFF_HOURS,
        s3_backend: BlobBackend | None = None,
    ) -> None:
        """Initialize the service with a connection factory and blob backend."""
        self._connection = connection_factory
        self._lock = lock
        self._max_size_bytes = max_size_bytes
        self._orphan_cutoff_hours = orphan_cutoff_hours
        self._s3_backend = s3_backend

    @property
    def blob_backend(self) -> str:
        """Return the configured blob backend name ("postgres" or "s3")."""
        return "s3" if self._s3_backend is not None else _DEFAULT_BLOB_BACKEND

    @property
    def s3_backend(self) -> BlobBackend | None:
        """Return the configured S3-compatible backend, if any."""
        return self._s3_backend

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    async def save_attachment(
        self,
        *,
        attachment_id: str | None = None,
        workspace_id: str,
        workflow_id: str,
        thread_id: str | None,
        upload_session_id: str | None,
        auth_mode: str,
        actor_subject: str | None,
        attachment_type: str,
        name: str,
        mime_type: str,
        content: bytes,
        details_json: str | None = None,
        blob_backend: str = _DEFAULT_BLOB_BACKEND,
    ) -> tuple[str, str | None]:
        """Persist metadata and blob in one transactional operation.

        Returns ``(attachment_id, upload_session_id)`` — the latter is only set
        when the backend minted a new upload-session id (i.e. neither
        ``thread_id`` nor a caller-supplied ``upload_session_id`` was provided).
        """
        if not thread_id and not upload_session_id:
            upload_session_id = _mint_upload_session_id()
            minted_session = upload_session_id
        else:
            minted_session = None

        if attachment_id is None:
            attachment_id = _mint_attachment_id()

        if details_json is None:
            details_json = compact_json(
                {
                    "id": attachment_id,
                    "name": name,
                    "mime_type": mime_type,
                    "type": attachment_type,
                }
            )

        size_bytes = len(content)
        if size_bytes > self._max_size_bytes:
            msg = (
                f"Attachment exceeds maximum allowed size of "
                f"{self._max_size_bytes} bytes"
            )
            raise ValueError(msg)

        digest = _sha256_hex(content)
        now = datetime.now(UTC)

        # Compute the storage key — S3 uses a namespaced path; Postgres uses the id.
        if blob_backend == "s3" and self._s3_backend is not None:
            computed_blob_key = self._s3_backend.make_key(workspace_id, attachment_id)
        else:
            computed_blob_key = attachment_id  # Postgres: blob_key == id

        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_attachments (
                        id, workspace_id, workflow_id, thread_id, upload_session_id,
                        auth_mode, actor_subject, attachment_type, name, mime_type,
                        size_bytes, sha256, details_json, blob_backend, blob_key,
                        storage_path, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        NULL, %s
                    )
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        attachment_id,
                        workspace_id,
                        workflow_id,
                        thread_id,
                        upload_session_id,
                        auth_mode,
                        actor_subject,
                        attachment_type,
                        name,
                        mime_type,
                        size_bytes,
                        digest,
                        details_json,
                        blob_backend,
                        computed_blob_key,
                        now,
                    ),
                )
                if blob_backend == _DEFAULT_BLOB_BACKEND:
                    await conn.execute(
                        """
                        INSERT INTO chat_attachment_blobs (
                            attachment_id, content, size_bytes, sha256, created_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT(attachment_id) DO NOTHING
                        """,
                        (attachment_id, content, size_bytes, digest, now),
                    )

        if blob_backend == "s3" and self._s3_backend is not None:
            await self._s3_backend.put(
                computed_blob_key, content, sha256=digest, size_bytes=size_bytes
            )

        return attachment_id, minted_session

    async def resolve_upload_session_id(
        self,
        attachment_ids: Iterable[str],
        workspace_id: str,
        *,
        workflow_id: str | None = None,
    ) -> str | None:
        """Return the common upload-session id for the given attachment ids.

        When the direct-upload client does not echo an upload-session id back to
        the message request, the server can infer it from the attachment rows as
        long as every attachment belongs to the same workspace-scoped session.
        """
        normalized_ids: list[str] = []
        for attachment_id in attachment_ids:
            normalized_id = attachment_id.strip()
            if normalized_id:
                normalized_ids.append(normalized_id)
        if not normalized_ids:
            return None

        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, workflow_id, thread_id, upload_session_id
                  FROM chat_attachments
                 WHERE id = ANY(%s)
                   AND (workspace_id = %s OR workspace_id IS NULL)
                """,
                (normalized_ids, workspace_id),
            )
            rows = await cursor.fetchall()

        if len(rows) != len({attachment_id for attachment_id in normalized_ids}):
            return None

        upload_session_ids: set[str] = set()
        for row in rows:
            row_workflow_id = row.get("workflow_id")
            if workflow_id and row_workflow_id and str(row_workflow_id) != workflow_id:
                return None
            session_id = row.get("upload_session_id")
            if not session_id:
                return None
            upload_session_ids.add(str(session_id))

        if len(upload_session_ids) != 1:
            return None
        return upload_session_ids.pop()

    # ------------------------------------------------------------------
    # Load (scoped)
    # ------------------------------------------------------------------

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> _AttachmentPayloadImpl:
        """Return attachment content after verifying scope.

        Returns a generic not-found error without revealing whether the id
        exists under a different scope.
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT a.id, a.name, a.mime_type, a.size_bytes, a.sha256,
                       a.blob_backend, a.blob_key, a.workflow_id,
                       a.thread_id, a.upload_session_id, a.details_json
                  FROM chat_attachments a
                 WHERE a.id = %s
                   AND (a.workspace_id = %s OR a.workspace_id IS NULL)
                """,
                (attachment_id, scope.workspace_id),
            )
            row = await cursor.fetchone()

        if row is None:
            raise AttachmentNotFoundError(attachment_id)

        if not self._scope_matches(row, scope):
            raise AttachmentNotFoundError(attachment_id)

        blob_backend = row.get("blob_backend") or None
        blob_key = row.get("blob_key") or None
        content = await self._load_blob(attachment_id, blob_backend, blob_key=blob_key)

        stored_sha256 = row.get("sha256") or ""
        if stored_sha256 and _sha256_hex(content) != stored_sha256:
            logger.error(
                "SHA-256 mismatch for attachment %s — stored %s",
                attachment_id,
                stored_sha256,
            )
            raise AttachmentNotFoundError(attachment_id)

        return _AttachmentPayloadImpl(
            id=row["id"],
            name=row["name"],
            mime_type=row["mime_type"],
            size_bytes=row.get("size_bytes") or len(content),
            sha256=stored_sha256,
            content=content,
            metadata={
                "workflow_id": row.get("workflow_id"),
                "thread_id": row.get("thread_id"),
            },
        )

    async def _load_blob(
        self,
        attachment_id: str,
        blob_backend: str | None,
        blob_key: str | None = None,
    ) -> bytes:
        if blob_backend == _DEFAULT_BLOB_BACKEND:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT content FROM chat_attachment_blobs "
                    "WHERE attachment_id = %s",
                    (attachment_id,),
                )
                row = await cursor.fetchone()
            if row is None:
                raise AttachmentNotFoundError(attachment_id)
            return bytes(row["content"])

        if blob_backend == "s3":
            if self._s3_backend is None:
                msg = "S3 blob backend not configured on this service instance."
                raise AttachmentNotFoundError(attachment_id)
            key = blob_key or attachment_id
            return await self._s3_backend.load(key)

        msg = f"Unsupported blob backend: {blob_backend!r}"
        raise NotImplementedError(msg)

    @staticmethod
    def _scope_matches(row: Any, scope: AttachmentScope) -> bool:
        """Return True if the attachment row is accessible within scope."""
        row_workflow_id = row.get("workflow_id")
        if (
            scope.workflow_id
            and row_workflow_id
            and str(row_workflow_id) != scope.workflow_id
        ):
            return False

        row_thread = row.get("thread_id")
        row_session = row.get("upload_session_id")
        if scope.thread_id and row_thread == scope.thread_id:
            return True
        if scope.upload_session_id and row_session == scope.upload_session_id:
            return True
        if scope.thread_id is None and scope.upload_session_id is None:
            return True
        return False

    async def load_attachment_bytes_public(
        self,
        attachment_id: str,
    ) -> _AttachmentPayloadImpl:
        """Return attachment content by id without scope enforcement.

        Intended for the unauthenticated download endpoint.  Security relies on
        the attachment id being unguessable (``atc_`` + 32 hex chars).
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT a.id, a.name, a.mime_type, a.size_bytes, a.sha256,
                       a.blob_backend, a.blob_key
                  FROM chat_attachments a
                 WHERE a.id = %s
                """,
                (attachment_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            raise AttachmentNotFoundError(attachment_id)

        blob_backend = row.get("blob_backend") or None
        blob_key = row.get("blob_key") or None
        content = await self._load_blob(attachment_id, blob_backend, blob_key=blob_key)

        stored_sha256 = row.get("sha256") or ""
        if stored_sha256 and _sha256_hex(content) != stored_sha256:
            logger.error(
                "SHA-256 mismatch for attachment %s — stored %s",
                attachment_id,
                stored_sha256,
            )
            raise AttachmentNotFoundError(attachment_id)

        return _AttachmentPayloadImpl(
            id=row["id"],
            name=row["name"],
            mime_type=row["mime_type"],
            size_bytes=row.get("size_bytes") or len(content),
            sha256=stored_sha256,
            content=content,
        )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_attachment(
        self,
        attachment_id: str,
        workspace_id: str | None,
    ) -> None:
        """Delete attachment metadata and blob together.

        For S3-backed attachments, the object is removed from S3 after the
        DB row is deleted so the metadata is never left dangling.
        """
        s3_key: str | None = None
        blob_backend: str | None = None
        async with self._lock:
            async with self._connection() as conn:
                if workspace_id is None:
                    cursor = await conn.execute(
                        """
                        SELECT blob_backend, blob_key
                          FROM chat_attachments
                         WHERE id = %s
                        """,
                        (attachment_id,),
                    )
                    row = await cursor.fetchone()
                    delete_params: tuple[str, ...] = (attachment_id,)
                    delete_sql = "DELETE FROM chat_attachments WHERE id = %s"
                else:
                    cursor = await conn.execute(
                        """
                        SELECT blob_backend, blob_key
                          FROM chat_attachments
                         WHERE id = %s AND workspace_id = %s
                        """,
                        (attachment_id, workspace_id),
                    )
                    row = await cursor.fetchone()
                    delete_params = (attachment_id, workspace_id)
                    delete_sql = (
                        "DELETE FROM chat_attachments WHERE id = %s "
                        "AND workspace_id = %s"
                    )

                if row:
                    blob_backend = row.get("blob_backend") or None
                if blob_backend == "s3":
                    s3_key = row.get("blob_key") or attachment_id

                await conn.execute(delete_sql, delete_params)

        if s3_key and self._s3_backend is not None:
            await self._s3_backend.delete(s3_key)

    # ------------------------------------------------------------------
    # Link upload-session to thread
    # ------------------------------------------------------------------

    async def link_upload_session_to_thread(
        self,
        upload_session_id: str,
        thread_id: str,
        workspace_id: str,
    ) -> int:
        """Bind all upload-session attachments to a thread id.

        Returns the number of rows updated.
        """
        now = datetime.now(UTC)
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE chat_attachments
                       SET thread_id = %s,
                           linked_at = %s
                     WHERE upload_session_id = %s
                       AND workspace_id = %s
                       AND thread_id IS NULL
                    """,
                    (thread_id, now, upload_session_id, workspace_id),
                )
                return cursor.rowcount if cursor.rowcount is not None else 0

    async def link_attachments_to_thread(
        self,
        attachment_ids: Iterable[str],
        thread_id: str,
        workspace_id: str,
    ) -> int:
        """Bind specific attachments to a thread id.

        Unlike :meth:`link_upload_session_to_thread`, this links by attachment id
        so it works when a single message references files uploaded across more
        than one upload session (a common upload session id cannot be resolved in
        that case). Returns the number of rows updated.
        """
        normalized_ids = [
            attachment_id.strip()
            for attachment_id in attachment_ids
            if attachment_id and attachment_id.strip()
        ]
        if not normalized_ids:
            return 0
        now = datetime.now(UTC)
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    UPDATE chat_attachments
                       SET thread_id = %s,
                           linked_at = %s
                     WHERE id = ANY(%s)
                       AND workspace_id = %s
                       AND thread_id IS NULL
                    """,
                    (thread_id, now, normalized_ids, workspace_id),
                )
                return cursor.rowcount if cursor.rowcount is not None else 0

    # ------------------------------------------------------------------
    # Prune orphaned upload sessions
    # ------------------------------------------------------------------

    async def prune_orphaned_upload_sessions(
        self,
        workspace_id: str | None = None,
        *,
        cutoff_hours: int | None = None,
    ) -> int:
        """Delete upload-session attachments that were never linked to a thread.

        An orphan satisfies: ``thread_id IS NULL AND linked_at IS NULL AND
        created_at < (now() - cutoff)``.
        Returns the number of rows deleted.
        """
        hours = cutoff_hours if cutoff_hours is not None else self._orphan_cutoff_hours
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        async with self._lock:
            async with self._connection() as conn:
                if workspace_id:
                    cursor = await conn.execute(
                        """
                        SELECT id, blob_backend, blob_key
                          FROM chat_attachments
                         WHERE thread_id IS NULL
                           AND linked_at IS NULL
                           AND upload_session_id IS NOT NULL
                           AND created_at < %s
                           AND workspace_id = %s
                        """,
                        (cutoff, workspace_id),
                    )
                    rows = await cursor.fetchall()
                    cursor = await conn.execute(
                        """
                        DELETE FROM chat_attachments
                         WHERE thread_id IS NULL
                           AND linked_at IS NULL
                           AND upload_session_id IS NOT NULL
                           AND created_at < %s
                           AND workspace_id = %s
                        """,
                        (cutoff, workspace_id),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT id, blob_backend, blob_key
                          FROM chat_attachments
                         WHERE thread_id IS NULL
                           AND linked_at IS NULL
                           AND upload_session_id IS NOT NULL
                           AND created_at < %s
                        """,
                        (cutoff,),
                    )
                    rows = await cursor.fetchall()
                    cursor = await conn.execute(
                        """
                        DELETE FROM chat_attachments
                         WHERE thread_id IS NULL
                           AND linked_at IS NULL
                           AND upload_session_id IS NOT NULL
                           AND created_at < %s
                        """,
                        (cutoff,),
                    )

        for row in rows:
            blob_backend = row.get("blob_backend") or None
            if blob_backend == "s3" and self._s3_backend is not None:
                await self._s3_backend.delete(row.get("blob_key") or row["id"])
                continue

        return cursor.rowcount if cursor.rowcount is not None else 0


class _ScopedResolver:
    """Adapts AttachmentService to the AttachmentResolver protocol for a fixed scope."""

    def __init__(self, service: AttachmentService, scope: _AttachmentScopeImpl) -> None:
        self._service = service
        self._scope = scope

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> _AttachmentPayloadImpl:
        return await self._service.load_attachment_bytes(attachment_id, self._scope)


def _resolve_download_base_url() -> str:
    """Return the base URL used to build attachment download links.

    Prefers ``ORCHEO_API_URL`` because that is the documented public backend
    origin.  ``ORCHEO_API_BASE_URL`` remains as a compatibility fallback for
    older deployments.  Falls back to an empty string only when neither is set.
    """
    raw = os.environ.get("ORCHEO_API_URL", "").strip()
    if not raw:
        raw = os.environ.get("ORCHEO_API_BASE_URL", "").strip()
    return raw.rstrip("/")


class _ScopedUploader:
    """Adapts AttachmentService to the AttachmentUploader protocol for a fixed scope."""

    def __init__(self, service: AttachmentService, scope: _AttachmentScopeImpl) -> None:
        self._service = service
        self._scope = scope

    async def upload_attachment(
        self,
        content: bytes,
        name: str,
        mime_type: str,
    ) -> tuple[str, str]:
        attachment_id, _ = await self._service.save_attachment(
            workspace_id=self._scope.workspace_id,
            workflow_id=self._scope.workflow_id or "",
            thread_id=self._scope.thread_id,
            upload_session_id=self._scope.upload_session_id,
            auth_mode="workflow",
            actor_subject=None,
            attachment_type="file",
            name=name,
            mime_type=mime_type,
            content=content,
            blob_backend=self._service.blob_backend,
        )
        base = _resolve_download_base_url()
        download_url = f"{base}/api/chatkit/attachments/{attachment_id}"
        return attachment_id, download_url


def build_attachment_scope(
    *,
    workspace_id: str,
    workflow_id: str | None = None,
    thread_id: str | None = None,
    upload_session_id: str | None = None,
) -> _AttachmentScopeImpl:
    """Create a trusted attachment scope from server-side context values."""
    return _AttachmentScopeImpl(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        thread_id=thread_id,
        upload_session_id=upload_session_id,
    )


def build_scoped_resolver(
    service: AttachmentService,
    scope: _AttachmentScopeImpl,
) -> _ScopedResolver:
    """Create an AttachmentResolver bound to *scope* for injection into workflows."""
    return _ScopedResolver(service, scope)


def build_scoped_uploader(
    service: AttachmentService,
    scope: _AttachmentScopeImpl,
) -> _ScopedUploader:
    """Create an AttachmentUploader bound to *scope* for injection into workflows."""
    return _ScopedUploader(service, scope)


# Ensure protocol conformance at import time
_resolver_check = cast(AttachmentResolver, _ScopedResolver.__new__(_ScopedResolver))
_uploader_check = cast(AttachmentUploader, _ScopedUploader.__new__(_ScopedUploader))


__all__ = [
    "AttachmentNotFoundError",
    "AttachmentService",
    "build_attachment_scope",
    "build_scoped_resolver",
    "build_scoped_uploader",
]
