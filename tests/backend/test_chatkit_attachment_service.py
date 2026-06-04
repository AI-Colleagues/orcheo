"""Unit tests for the ChatKit attachment service and schema helpers."""

from __future__ import annotations
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
    AttachmentNotFoundError,
    AttachmentService,
    _sha256_hex,
    _mint_attachment_id,
    _mint_upload_session_id,
    build_attachment_scope,
    build_scoped_resolver,
    build_scoped_uploader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeRow(dict):
    """Dict subclass that supports .get() — used as a fake DB row."""


def _make_fake_conn(rows: list[_FakeRow]) -> Any:
    """Return a minimal mock psycopg connection."""
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=rows[0] if rows else None)
    cursor.fetchall = AsyncMock(return_value=rows)
    cursor.rowcount = len(rows)

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=cursor)
    return conn, cursor


@asynccontextmanager
async def _conn_ctx(conn: Any):
    yield conn


def _make_service(
    rows: list[_FakeRow] | None = None,
    *,
    max_size_bytes: int = 10 * 1024 * 1024,
    orphan_cutoff_hours: int = 24,
) -> tuple[AttachmentService, Any, Any]:
    conn, cursor = _make_fake_conn(rows or [])

    def _factory():
        return _conn_ctx(conn)

    lock = asyncio.Lock()
    service = AttachmentService(
        _factory,
        lock,
        max_size_bytes=max_size_bytes,
        orphan_cutoff_hours=orphan_cutoff_hours,
    )
    return service, conn, cursor


# ---------------------------------------------------------------------------
# _sha256_hex
# ---------------------------------------------------------------------------


def test_sha256_hex_correctness() -> None:
    data = b"hello world"
    assert _sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_sha256_hex_empty() -> None:
    result = _sha256_hex(b"")
    assert len(result) == 64


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def test_mint_attachment_id_prefix() -> None:
    aid = _mint_attachment_id()
    assert aid.startswith("atc_")
    assert len(aid) > 4


def test_mint_upload_session_id_prefix() -> None:
    sid = _mint_upload_session_id()
    assert sid.startswith("ups_")
    assert len(sid) > 4


def test_mint_ids_are_unique() -> None:
    ids = {_mint_attachment_id() for _ in range(20)}
    assert len(ids) == 20


# ---------------------------------------------------------------------------
# AttachmentService.save_attachment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_attachment_calls_insert_twice() -> None:
    """save_attachment inserts one metadata row and one blob row."""
    service, conn, _ = _make_service()

    atc_id, session_id = await service.save_attachment(
        workspace_id="ws1",
        workflow_id="wf1",
        thread_id="thread1",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="doc.txt",
        mime_type="text/plain",
        content=b"hello",
    )

    assert atc_id.startswith("atc_")
    assert session_id is None
    assert conn.execute.call_count == 2


@pytest.mark.asyncio
async def test_save_attachment_mints_session_when_no_scope() -> None:
    """When neither thread_id nor upload_session_id is given, a session is minted."""
    service, conn, _ = _make_service()

    atc_id, session_id = await service.save_attachment(
        workspace_id="ws1",
        workflow_id="wf1",
        thread_id=None,
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="doc.txt",
        mime_type="text/plain",
        content=b"data",
    )

    assert session_id is not None
    assert session_id.startswith("ups_")


@pytest.mark.asyncio
async def test_save_attachment_raises_when_too_large() -> None:
    """save_attachment raises ValueError when content exceeds max_size_bytes."""
    service, _, _ = _make_service(max_size_bytes=3)

    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        await service.save_attachment(
            workspace_id="ws",
            workflow_id="wf",
            thread_id="t",
            upload_session_id=None,
            auth_mode="publish",
            actor_subject=None,
            attachment_type="file",
            name="big.txt",
            mime_type="text/plain",
            content=b"toolarge",
        )


@pytest.mark.asyncio
async def test_save_attachment_uses_provided_attachment_id() -> None:
    """A caller-supplied attachment_id is used as-is."""
    service, conn, _ = _make_service()

    atc_id, _ = await service.save_attachment(
        attachment_id="atc_custom123",
        workspace_id="ws",
        workflow_id="wf",
        thread_id="t",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="f.txt",
        mime_type="text/plain",
        content=b"x",
    )

    assert atc_id == "atc_custom123"


@pytest.mark.asyncio
async def test_save_attachment_populates_details_json_when_omitted() -> None:
    """New attachments should persist discriminated details metadata."""
    service, conn, _ = _make_service()

    atc_id, _ = await service.save_attachment(
        workspace_id="ws",
        workflow_id="wf",
        thread_id="t",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="f.txt",
        mime_type="text/plain",
        content=b"x",
    )

    params = conn.execute.call_args_list[0].args[1]
    details = json.loads(params[12])

    assert details["id"] == atc_id
    assert details["name"] == "f.txt"
    assert details["mime_type"] == "text/plain"
    assert details["type"] == "file"


@pytest.mark.asyncio
async def test_save_attachment_preserves_explicit_details_json() -> None:
    service, conn, _ = _make_service()
    explicit_details = '{"id":"custom","name":"explicit"}'

    await service.save_attachment(
        workspace_id="ws",
        workflow_id="wf",
        thread_id="t",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="f.txt",
        mime_type="text/plain",
        content=b"x",
        details_json=explicit_details,
    )

    params = conn.execute.call_args_list[0].args[1]
    assert params[12] == explicit_details


def test_attachment_service_blob_backend_properties() -> None:
    service, _, _ = _make_service()
    assert service.blob_backend == "postgres"
    assert service.s3_backend is None

    s3_service = AttachmentService(
        service._connection,
        service._lock,
        s3_backend=MagicMock(),
    )
    assert s3_service.blob_backend == "s3"
    assert s3_service.s3_backend is not None


@pytest.mark.asyncio
async def test_scoped_uploader_uses_documented_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download URLs should point at the backend API origin, not the Canvas host."""
    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com")
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)

    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_123", None))
    scope = build_attachment_scope(workspace_id="ws1", workflow_id="wf1")
    uploader = build_scoped_uploader(service, scope)

    attachment_id, download_url = await uploader.upload_attachment(
        b"hello", "doc.txt", "text/plain"
    )

    assert attachment_id == "atc_123"
    assert download_url == "https://api.example.com/api/chatkit/attachments/atc_123"
    service.save_attachment.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoped_uploader_keeps_legacy_api_base_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older deployments that still set ORCHEO_API_BASE_URL continue to work."""
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.setenv("ORCHEO_API_BASE_URL", "https://legacy.example.com")

    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_456", None))
    scope = build_attachment_scope(workspace_id="ws1", workflow_id="wf1")
    uploader = build_scoped_uploader(service, scope)

    _, download_url = await uploader.upload_attachment(
        b"hello", "doc.txt", "text/plain"
    )

    assert download_url == "https://legacy.example.com/api/chatkit/attachments/atc_456"


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_none_for_empty_ids() -> None:
    service, conn, _ = _make_service(rows=[])

    result = await service.resolve_upload_session_id([" ", ""], "ws1")

    assert result is None
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_none_when_row_count_mismatches() -> (
    None
):
    rows = [
        _FakeRow(
            id="atc_1",
            workflow_id="wf1",
            thread_id="t1",
            upload_session_id="ups_1",
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_upload_session_id(["atc_1", "atc_2"], "ws1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_none_for_workflow_mismatch() -> None:
    rows = [
        _FakeRow(
            id="atc_1",
            workflow_id="wf_A",
            thread_id="t1",
            upload_session_id="ups_1",
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_upload_session_id(
        ["atc_1"], "ws1", workflow_id="wf_B"
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_none_for_missing_session() -> None:
    rows = [
        _FakeRow(
            id="atc_1",
            workflow_id="wf1",
            thread_id="t1",
            upload_session_id=None,
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_upload_session_id(["atc_1"], "ws1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_none_for_multiple_sessions() -> None:
    rows = [
        _FakeRow(
            id="atc_1",
            workflow_id="wf1",
            thread_id="t1",
            upload_session_id="ups_1",
        ),
        _FakeRow(
            id="atc_2",
            workflow_id="wf1",
            thread_id="t1",
            upload_session_id="ups_2",
        ),
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_upload_session_id(["atc_1", "atc_2"], "ws1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_upload_session_id_returns_common_session() -> None:
    rows = [
        _FakeRow(
            id="atc_1",
            workflow_id="wf1",
            thread_id="t1",
            upload_session_id="ups_1",
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_upload_session_id(["atc_1"], "ws1")

    assert result == "ups_1"


# ---------------------------------------------------------------------------
# AttachmentService.load_attachment_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_attachment_bytes_returns_payload() -> None:
    content = b"document content"
    digest = _sha256(content)

    meta_row = _FakeRow(
        id="atc_1",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256=digest,
        blob_backend="postgres",
        storage_path=None,
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )
    blob_row = _FakeRow(content=content)

    call_count = 0
    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cursor = MagicMock()
        if call_count == 1:
            # metadata query
            cursor.fetchone = AsyncMock(return_value=meta_row)
        else:
            # blob query
            cursor.fetchone = AsyncMock(return_value=blob_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    scope = build_attachment_scope(
        workspace_id="ws1",
        workflow_id="wf1",
        thread_id="t1",
    )
    payload = await service.load_attachment_bytes("atc_1", scope)

    assert payload.id == "atc_1"
    assert payload.content == content
    assert payload.sha256 == digest


@pytest.mark.asyncio
async def test_load_attachment_bytes_rejects_sha_mismatch() -> None:
    content = b"document content"
    meta_row = _FakeRow(
        id="atc_1",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256="0" * 64,
        blob_backend="postgres",
        storage_path=None,
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )
    blob_row = _FakeRow(content=content)

    call_count = 0
    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cursor = MagicMock()
        if call_count == 1:
            cursor.fetchone = AsyncMock(return_value=meta_row)
        else:
            cursor.fetchone = AsyncMock(return_value=blob_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)
    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf1", thread_id="t1"
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_1", scope)


@pytest.mark.asyncio
async def test_load_attachment_bytes_public_returns_payload() -> None:
    content = b"public content"
    digest = _sha256(content)

    meta_row = _FakeRow(
        id="atc_public",
        name="public.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256=digest,
        blob_backend="postgres",
        blob_key="atc_public",
    )
    blob_row = _FakeRow(content=content)

    call_count = 0
    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cursor = MagicMock()
        if call_count == 1:
            cursor.fetchone = AsyncMock(return_value=meta_row)
        else:
            cursor.fetchone = AsyncMock(return_value=blob_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    payload = await service.load_attachment_bytes_public("atc_public")

    assert payload.id == "atc_public"
    assert payload.content == content
    assert payload.sha256 == digest


@pytest.mark.asyncio
async def test_load_attachment_bytes_public_rejects_sha_mismatch() -> None:
    content = b"public content"
    meta_row = _FakeRow(
        id="atc_public",
        name="public.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256="1" * 64,
        blob_backend="postgres",
        blob_key="atc_public",
    )
    blob_row = _FakeRow(content=content)

    call_count = 0
    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cursor = MagicMock()
        if call_count == 1:
            cursor.fetchone = AsyncMock(return_value=meta_row)
        else:
            cursor.fetchone = AsyncMock(return_value=blob_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes_public("atc_public")


@pytest.mark.asyncio
async def test_load_attachment_bytes_public_raises_when_row_missing() -> None:
    service, _, _ = _make_service(rows=[])

    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes_public("atc_missing")


@pytest.mark.asyncio
async def test_load_blob_rejects_missing_postgres_blob_row() -> None:
    service, _, _ = _make_service(rows=[])

    with pytest.raises(AttachmentNotFoundError):
        await service._load_blob("atc_missing", "postgres")


@pytest.mark.asyncio
async def test_load_blob_rejects_s3_without_backend() -> None:
    service, _, _ = _make_service(rows=[])

    with pytest.raises(AttachmentNotFoundError):
        await service._load_blob("atc_missing", "s3")


@pytest.mark.asyncio
async def test_load_attachment_bytes_raises_not_found_when_missing() -> None:
    service, _, _ = _make_service(rows=[])

    scope = build_attachment_scope(workspace_id="ws1")
    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_missing", scope)


@pytest.mark.asyncio
async def test_load_attachment_bytes_raises_for_wrong_workspace() -> None:
    """Attachment in workspace A is not visible to scope with workspace B."""
    content = b"secret"
    meta_row = _FakeRow(
        id="atc_secret",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256=_sha256(content),
        blob_backend="postgres",
        storage_path=None,
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )

    conn, cursor = _make_fake_conn([])

    # First call (metadata) returns empty → not found
    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    scope = build_attachment_scope(workspace_id="workspace_B")
    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_secret", scope)


# ---------------------------------------------------------------------------
# Unsupported legacy filesystem rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_attachment_bytes_rejects_legacy_filesystem_rows() -> None:
    """Rows without a blob backend no longer fall back to filesystem reads."""
    meta_row = _FakeRow(
        id="atc_legacy",
        name="legacy.txt",
        mime_type="text/plain",
        size_bytes=19,
        sha256="",
        blob_backend=None,
        storage_path="/tmp/legacy.txt",
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )

    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=meta_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)
    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf1", thread_id="t1"
    )

    with pytest.raises(NotImplementedError, match="Unsupported blob backend"):
        await service.load_attachment_bytes("atc_legacy", scope)


# ---------------------------------------------------------------------------
# Scope predicate
# ---------------------------------------------------------------------------


def test_scope_matches_exact_thread() -> None:
    row = _FakeRow(workflow_id="wf1", thread_id="t1", upload_session_id=None)
    scope = build_attachment_scope(workspace_id="ws", workflow_id="wf1", thread_id="t1")
    assert AttachmentService._scope_matches(row, scope)


def test_scope_matches_upload_session() -> None:
    row = _FakeRow(workflow_id="wf1", thread_id=None, upload_session_id="ups_abc")
    scope = build_attachment_scope(
        workspace_id="ws", workflow_id="wf1", upload_session_id="ups_abc"
    )
    assert AttachmentService._scope_matches(row, scope)


def test_scope_rejects_wrong_workflow() -> None:
    row = _FakeRow(workflow_id="wf_A", thread_id="t1", upload_session_id=None)
    scope = build_attachment_scope(
        workspace_id="ws", workflow_id="wf_B", thread_id="t1"
    )
    assert not AttachmentService._scope_matches(row, scope)


def test_scope_rejects_wrong_session() -> None:
    row = _FakeRow(workflow_id="wf1", thread_id=None, upload_session_id="ups_A")
    scope = build_attachment_scope(
        workspace_id="ws", workflow_id="wf1", upload_session_id="ups_B"
    )
    assert not AttachmentService._scope_matches(row, scope)


def test_scope_matches_without_thread_or_session_is_permissive() -> None:
    row = _FakeRow(workflow_id="wf1", thread_id="thread-1", upload_session_id="ups_A")
    scope = build_attachment_scope(workspace_id="ws", workflow_id="wf1")
    assert AttachmentService._scope_matches(row, scope)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_attachment_executes_delete() -> None:
    service, conn, _ = _make_service()

    await service.delete_attachment("atc_1", "ws1")

    assert conn.execute.call_count == 2
    select_sql, select_params = conn.execute.call_args_list[0][0]
    delete_sql, delete_params = conn.execute.call_args_list[1][0]
    assert "SELECT" in select_sql
    assert "atc_1" in select_params
    assert "DELETE" in delete_sql
    assert "atc_1" in delete_params


@pytest.mark.asyncio
async def test_delete_attachment_without_workspace_uses_id_only() -> None:
    select_row = _FakeRow(blob_backend="postgres", blob_key="atc_only")
    first_cursor = MagicMock()
    first_cursor.fetchone = AsyncMock(return_value=select_row)
    second_cursor = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=[first_cursor, second_cursor])

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    await service.delete_attachment("atc_only", None)

    assert conn.execute.call_args_list[0].args[0].strip().startswith("SELECT")
    assert conn.execute.call_args_list[0].args[1] == ("atc_only",)
    assert conn.execute.call_args_list[1].args[0].strip() == (
        "DELETE FROM chat_attachments WHERE id = %s"
    )
    assert conn.execute.call_args_list[1].args[1] == ("atc_only",)


# ---------------------------------------------------------------------------
# Link upload session to thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_upload_session_returns_row_count() -> None:
    conn, cursor = _make_fake_conn([])
    cursor.rowcount = 3

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    count = await service.link_upload_session_to_thread(
        upload_session_id="ups_abc",
        thread_id="thread_1",
        workspace_id="ws1",
    )
    assert count == 3


@pytest.mark.asyncio
async def test_link_attachments_to_thread_returns_row_count() -> None:
    conn, cursor = _make_fake_conn([])
    cursor.rowcount = 2

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    count = await service.link_attachments_to_thread(
        ["atc_1", "atc_2"],
        "thread_1",
        "ws1",
    )
    assert count == 2
    update_sql, update_params = conn.execute.call_args_list[0][0]
    assert "UPDATE chat_attachments" in update_sql
    assert update_params[2] == ["atc_1", "atc_2"]


@pytest.mark.asyncio
async def test_link_attachments_to_thread_skips_query_when_empty() -> None:
    conn, cursor = _make_fake_conn([])

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    count = await service.link_attachments_to_thread(["", "  "], "thread_1", "ws1")
    assert count == 0
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Prune orphaned upload sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_orphaned_upload_sessions_executes_delete() -> None:
    conn, cursor = _make_fake_conn([])
    cursor.rowcount = 5

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, orphan_cutoff_hours=48)

    count = await service.prune_orphaned_upload_sessions("ws1", cutoff_hours=48)
    assert count == 5
    sql, params = conn.execute.call_args[0]
    assert "DELETE" in sql
    assert "upload_session_id IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_prune_orphaned_upload_sessions_workspace_none() -> None:
    """Pruning without a workspace_id uses a simpler query."""
    conn, cursor = _make_fake_conn([])
    cursor.rowcount = 2

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock)

    count = await service.prune_orphaned_upload_sessions(None)
    assert count == 2


@pytest.mark.asyncio
async def test_prune_orphaned_upload_sessions_deletes_s3_blobs() -> None:
    s3_backend = MagicMock()
    s3_backend.delete = AsyncMock()
    rows = [
        _FakeRow(
            id="atc_s3",
            blob_backend="s3",
            blob_key="attachments/ws1/atc_s3",
        ),
        _FakeRow(id="atc_pg", blob_backend="postgres", blob_key="atc_pg"),
    ]
    conn, cursor = _make_fake_conn(rows)
    cursor.rowcount = 2

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, s3_backend=s3_backend)

    count = await service.prune_orphaned_upload_sessions("ws1")

    assert count == 2
    s3_backend.delete.assert_awaited_once_with("attachments/ws1/atc_s3")


# ---------------------------------------------------------------------------
# build_attachment_scope
# ---------------------------------------------------------------------------


def test_build_attachment_scope_sets_fields() -> None:
    scope = build_attachment_scope(
        workspace_id="ws",
        workflow_id="wf",
        thread_id="t",
        upload_session_id="ups",
    )
    assert scope.workspace_id == "ws"
    assert scope.workflow_id == "wf"
    assert scope.thread_id == "t"
    assert scope.upload_session_id == "ups"


def test_build_attachment_scope_defaults() -> None:
    scope = build_attachment_scope(workspace_id="ws")
    assert scope.workflow_id is None
    assert scope.thread_id is None
    assert scope.upload_session_id is None


# ---------------------------------------------------------------------------
# build_scoped_resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_resolver_delegates_to_service() -> None:
    service = MagicMock()
    service.load_attachment_bytes = AsyncMock(return_value=MagicMock())
    scope = build_attachment_scope(workspace_id="ws")
    resolver = build_scoped_resolver(service, scope)

    await resolver.load_attachment_bytes("atc_1", scope)

    service.load_attachment_bytes.assert_awaited_once_with("atc_1", scope)


# ---------------------------------------------------------------------------
# resolve_recent_upload_session_id (lines 315, 319)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_recent_upload_returns_none_when_no_rows() -> None:
    """Returns None when 0 rows returned — len(rows) != 1 path (line 315)."""
    service, _, _ = _make_service(rows=[])

    result = await service.resolve_recent_upload_session_id(
        "ws-1",
        "wf-1",
        actor_subject="user@example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_recent_upload_returns_none_when_multiple_rows() -> None:
    """Returns None when 2 rows returned — len(rows) != 1 path (line 315)."""
    rows = [
        _FakeRow(upload_session_id="ups_1"),
        _FakeRow(upload_session_id="ups_2"),
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_recent_upload_session_id(
        "ws-1",
        "wf-1",
        actor_subject="user@example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_recent_upload_returns_none_when_session_id_blank() -> None:
    """Returns None when session_id is blank string (line 319)."""
    rows = [_FakeRow(upload_session_id="   ")]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_recent_upload_session_id(
        "ws-1",
        "wf-1",
        actor_subject="user@example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_recent_upload_returns_none_when_session_id_not_string() -> None:
    """Returns None when session_id is not a string (line 319)."""
    rows = [_FakeRow(upload_session_id=None)]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_recent_upload_session_id(
        "ws-1",
        "wf-1",
        actor_subject="user@example.com",
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_recent_upload_returns_stripped_session_id() -> None:
    """Returns stripped session_id when exactly one row with valid session_id."""
    rows = [_FakeRow(upload_session_id="  ups_abc  ")]
    service, _, _ = _make_service(rows=rows)

    result = await service.resolve_recent_upload_session_id(
        "ws-1",
        "wf-1",
        actor_subject="user@example.com",
    )

    assert result == "ups_abc"


# ---------------------------------------------------------------------------
# list_attachment_summaries (lines 486-523)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_attachment_summaries_returns_empty_without_scope() -> None:
    """Returns [] immediately when neither thread_id nor upload_session_id given (line 500)."""
    service, conn, _ = _make_service(rows=[])

    result = await service.list_attachment_summaries(
        workspace_id="ws-1",
        workflow_id="wf-1",
    )

    assert result == []
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_attachment_summaries_by_thread_id() -> None:
    """Returns attachment rows when filtered by thread_id."""
    rows = [
        _FakeRow(
            id="atc_1",
            name="report.csv",
            mime_type="text/csv",
            size_bytes=1024,
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.list_attachment_summaries(
        workspace_id="ws-1",
        workflow_id="wf-1",
        thread_id="thr-1",
    )

    assert len(result) == 1
    assert result[0]["id"] == "atc_1"
    assert result[0]["filename"] == "report.csv"
    assert result[0]["content_type"] == "text/csv"
    assert result[0]["size"] == 1024


@pytest.mark.asyncio
async def test_list_attachment_summaries_by_upload_session_id() -> None:
    """Returns attachment rows when filtered by upload_session_id."""
    rows = [
        _FakeRow(
            id="atc_2",
            name="data.json",
            mime_type="application/json",
            size_bytes=512,
        )
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.list_attachment_summaries(
        workspace_id="ws-1",
        upload_session_id="ups-1",
    )

    assert len(result) == 1
    assert result[0]["id"] == "atc_2"


@pytest.mark.asyncio
async def test_list_attachment_summaries_by_thread_and_session() -> None:
    """Combines thread_id and upload_session_id into an OR clause."""
    rows = [
        _FakeRow(id="atc_3", name="a.txt", mime_type="text/plain", size_bytes=100),
        _FakeRow(id="atc_4", name="b.txt", mime_type="text/plain", size_bytes=200),
    ]
    service, _, _ = _make_service(rows=rows)

    result = await service.list_attachment_summaries(
        workspace_id="ws-1",
        thread_id="thr-2",
        upload_session_id="ups-2",
    )

    assert len(result) == 2
    assert {r["id"] for r in result} == {"atc_3", "atc_4"}


@pytest.mark.asyncio
async def test_list_attachment_summaries_returns_empty_rows() -> None:
    """Returns [] when DB returns no matching rows."""
    service, _, _ = _make_service(rows=[])

    result = await service.list_attachment_summaries(
        workspace_id="ws-1",
        thread_id="thr-empty",
    )

    assert result == []
