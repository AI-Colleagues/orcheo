"""Tests for S3 blob backend and scope-before-fetch enforcement (Task 6.4)."""

from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch
import pytest
from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
    AttachmentNotFoundError,
    AttachmentService,
    _ScopedUploader,
    build_attachment_scope,
)
from orcheo_backend.app.chatkit_store_postgres.blob_backends import (
    BlobBackend,
    S3BlobBackend,
    build_blob_backend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """Dict subclass used as a fake DB row."""


def _make_s3_backend(
    *,
    bucket: str = "test-bucket",
    put_result: Any = None,
    load_result: bytes = b"s3 content",
    delete_result: Any = None,
) -> tuple[S3BlobBackend, MagicMock]:
    """Return an S3BlobBackend with a mocked boto3 client."""
    backend = S3BlobBackend(bucket, region="us-east-1")
    mock_client = MagicMock()
    mock_client.put_object = MagicMock(return_value=put_result)
    body_mock = MagicMock()
    body_mock.read = MagicMock(return_value=load_result)
    mock_client.get_object = MagicMock(return_value={"Body": body_mock})
    mock_client.delete_object = MagicMock(return_value=delete_result)
    backend._client = mock_client
    return backend, mock_client


def _make_conn(rows: list[_FakeRow]) -> tuple[MagicMock, MagicMock]:
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
    s3_backend: BlobBackend | None = None,
) -> tuple[AttachmentService, MagicMock, MagicMock]:
    conn, cursor = _make_conn(rows or [])

    def _factory():
        return _conn_ctx(conn)

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, s3_backend=s3_backend)
    return service, conn, cursor


@pytest.mark.asyncio
async def test_blob_backend_protocol_methods_are_callable() -> None:
    dummy = object()

    assert BlobBackend.make_key(dummy, "ws_1", "atc_1") is None
    await BlobBackend.put(dummy, "key", b"data", sha256="abc", size_bytes=4)
    assert await BlobBackend.load(dummy, "key") is None
    assert await BlobBackend.delete(dummy, "key") is None


# ---------------------------------------------------------------------------
# build_blob_backend
# ---------------------------------------------------------------------------


def test_build_blob_backend_postgres_returns_none() -> None:
    assert build_blob_backend("postgres") is None


def test_build_blob_backend_s3_returns_instance() -> None:
    backend = build_blob_backend("s3", bucket="my-bucket")
    assert isinstance(backend, S3BlobBackend)


def test_build_blob_backend_s3_requires_bucket() -> None:
    with pytest.raises(ValueError, match="ORCHEO_CHATKIT_S3_BUCKET"):
        build_blob_backend("s3", bucket=None)


def test_build_blob_backend_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown blob backend"):
        build_blob_backend("gcs")


# ---------------------------------------------------------------------------
# S3BlobBackend.make_key
# ---------------------------------------------------------------------------


def test_s3_make_key_format() -> None:
    backend = S3BlobBackend("bucket")
    key = backend.make_key("ws_abc", "atc_xyz")
    assert key == "attachments/ws_abc/atc_xyz"


# ---------------------------------------------------------------------------
# S3BlobBackend.put
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_put_calls_put_object() -> None:
    backend, mock_client = _make_s3_backend()
    await backend.put("attachments/ws/atc_1", b"hello", sha256="abc", size_bytes=5)

    mock_client.put_object.assert_called_once()
    kwargs = mock_client.put_object.call_args[1]
    assert kwargs["Bucket"] == "test-bucket"
    assert kwargs["Key"] == "attachments/ws/atc_1"
    assert kwargs["Body"] == b"hello"
    assert kwargs["Metadata"]["sha256"] == "abc"


# ---------------------------------------------------------------------------
# S3BlobBackend.load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_load_returns_bytes() -> None:
    backend, mock_client = _make_s3_backend(load_result=b"s3 data")
    result = await backend.load("attachments/ws/atc_1")
    assert result == b"s3 data"
    mock_client.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="attachments/ws/atc_1"
    )


# ---------------------------------------------------------------------------
# S3BlobBackend.delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_delete_calls_delete_object() -> None:
    backend, mock_client = _make_s3_backend()
    await backend.delete("attachments/ws/atc_1")
    mock_client.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="attachments/ws/atc_1"
    )


@pytest.mark.asyncio
async def test_s3_delete_swallows_errors() -> None:
    """delete() should not raise even if S3 fails."""
    backend, mock_client = _make_s3_backend()
    mock_client.delete_object.side_effect = RuntimeError("S3 error")
    # Should not raise
    await backend.delete("attachments/ws/atc_1")


# ---------------------------------------------------------------------------
# S3BlobBackend lazy client creation
# ---------------------------------------------------------------------------


def test_s3_get_client_raises_without_boto3() -> None:
    """Importing boto3 fails → RuntimeError with install hint."""
    backend = S3BlobBackend("bucket")
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(RuntimeError, match="boto3 must be installed"):
            backend._get_client()


def test_s3_get_client_passes_configuration_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _client(service_name: str, **kwargs: Any) -> object:
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=_client),
    )

    backend = S3BlobBackend(
        "bucket",
        endpoint_url="https://example.invalid",
        region="eu-west-1",
        access_key_id="key",
        secret_access_key="secret",
    )

    client = backend._get_client()

    assert client is backend._client
    assert calls == [
        (
            "s3",
            {
                "endpoint_url": "https://example.invalid",
                "region_name": "eu-west-1",
                "aws_access_key_id": "key",
                "aws_secret_access_key": "secret",
            },
        )
    ]


def test_s3_get_client_omits_optional_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _client(service_name: str, **kwargs: Any) -> object:
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=_client),
    )

    backend = S3BlobBackend("bucket")

    client = backend._get_client()

    assert client is backend._client
    assert calls == [("s3", {})]


# ---------------------------------------------------------------------------
# Task 6.4: scope checks happen BEFORE S3 fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_check_before_s3_load_cross_workspace() -> None:
    """Cross-workspace scope rejection must not trigger an S3 read."""
    s3_backend, _ = _make_s3_backend()
    s3_put = AsyncMock()
    s3_load = AsyncMock(return_value=b"secret")
    s3_backend.put = s3_put  # type: ignore[method-assign]
    s3_backend.load = s3_load  # type: ignore[method-assign]

    # DB returns nothing — workspace_B doesn't have atc_secret
    service, _, _ = _make_service(rows=[], s3_backend=s3_backend)

    scope = build_attachment_scope(workspace_id="workspace_B")
    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_secret", scope)

    s3_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_scope_check_before_s3_load_wrong_workflow() -> None:
    """Wrong-workflow scope rejection must not trigger an S3 read."""
    s3_backend, _ = _make_s3_backend()
    s3_load = AsyncMock(return_value=b"secret")
    s3_backend.load = s3_load  # type: ignore[method-assign]

    meta_row = _FakeRow(
        id="atc_secret",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=6,
        sha256="x" * 64,
        blob_backend="s3",
        blob_key="attachments/ws1/atc_secret",
        storage_path=None,
        workflow_id="wf_A",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )
    service, _, _ = _make_service(rows=[meta_row], s3_backend=s3_backend)

    # Request with wf_B — should fail scope check
    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf_B", thread_id="t1"
    )
    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_secret", scope)

    s3_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_scope_check_before_s3_load_wrong_thread() -> None:
    """Wrong-thread scope rejection must not trigger an S3 read."""
    s3_backend, _ = _make_s3_backend()
    s3_load = AsyncMock(return_value=b"secret")
    s3_backend.load = s3_load  # type: ignore[method-assign]

    meta_row = _FakeRow(
        id="atc_secret",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=6,
        sha256="x" * 64,
        blob_backend="s3",
        blob_key="attachments/ws1/atc_secret",
        storage_path=None,
        workflow_id="wf1",
        thread_id="thread_A",
        upload_session_id=None,
        details_json="{}",
    )
    service, _, _ = _make_service(rows=[meta_row], s3_backend=s3_backend)

    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf1", thread_id="thread_B"
    )
    with pytest.raises(AttachmentNotFoundError):
        await service.load_attachment_bytes("atc_secret", scope)

    s3_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_s3_load_succeeds_with_valid_scope() -> None:
    """Valid scope + S3 backend → bytes returned, no AttachmentNotFoundError."""
    import hashlib

    content = b"hello from s3"
    digest = hashlib.sha256(content).hexdigest()

    s3_backend, _ = _make_s3_backend(load_result=content)

    meta_row = _FakeRow(
        id="atc_1",
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        sha256=digest,
        blob_backend="s3",
        blob_key="attachments/ws1/atc_1",
        storage_path=None,
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        details_json="{}",
    )

    call_count = 0
    conn = MagicMock()

    async def _execute(sql: str, params: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=meta_row)
        return cursor

    conn.execute = _execute

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, s3_backend=s3_backend)
    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf1", thread_id="t1"
    )
    payload = await service.load_attachment_bytes("atc_1", scope)

    assert payload.content == content
    assert payload.id == "atc_1"


# ---------------------------------------------------------------------------
# save_attachment with S3 backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_attachment_puts_to_s3_when_configured() -> None:
    """save_attachment with blob_backend='s3' uploads to S3 after the DB insert."""
    s3_backend, _ = _make_s3_backend()
    s3_put = AsyncMock()
    s3_backend.put = s3_put  # type: ignore[method-assign]

    service, conn, _ = _make_service(s3_backend=s3_backend)

    atc_id, _ = await service.save_attachment(
        workspace_id="ws1",
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="doc.txt",
        mime_type="text/plain",
        content=b"data",
        blob_backend="s3",
    )

    # One INSERT for metadata; no INSERT for blob table (S3 handles bytes)
    assert conn.execute.call_count == 1

    s3_put.assert_awaited_once()
    put_args = s3_put.call_args
    key_arg = put_args[0][0]
    assert key_arg == f"attachments/ws1/{atc_id}"
    assert put_args[1]["size_bytes"] == 4


@pytest.mark.asyncio
async def test_save_attachment_uses_s3_key_as_blob_key() -> None:
    """blob_key in the DB INSERT must be the S3 path, not the attachment id alone."""
    s3_backend, _ = _make_s3_backend()
    s3_put = AsyncMock()
    s3_backend.put = s3_put  # type: ignore[method-assign]

    service, conn, _ = _make_service(s3_backend=s3_backend)

    atc_id, _ = await service.save_attachment(
        workspace_id="ws1",
        workflow_id="wf1",
        thread_id="t1",
        upload_session_id=None,
        auth_mode="publish",
        actor_subject=None,
        attachment_type="file",
        name="f.txt",
        mime_type="text/plain",
        content=b"x",
        blob_backend="s3",
    )

    # Inspect the INSERT SQL params — blob_key is at index 14 (0-based)
    insert_sql, insert_params = conn.execute.call_args[0]
    assert "INSERT INTO chat_attachments" in insert_sql
    expected_blob_key = f"attachments/ws1/{atc_id}"
    assert expected_blob_key in insert_params


# ---------------------------------------------------------------------------
# delete_attachment with S3 backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_attachment_removes_s3_object() -> None:
    """delete_attachment must call S3 delete when blob_backend='s3'."""
    s3_backend, _ = _make_s3_backend()
    s3_delete = AsyncMock()
    s3_backend.delete = s3_delete  # type: ignore[method-assign]

    # First conn.execute (SELECT) returns the row; second (DELETE) returns empty
    select_row = _FakeRow(blob_backend="s3", blob_key="attachments/ws1/atc_del")
    conn, _ = _make_conn([select_row])
    # Second call (DELETE) returns empty cursor
    delete_cursor = MagicMock()
    delete_cursor.rowcount = 1
    conn.execute = AsyncMock(
        side_effect=[
            MagicMock(fetchone=AsyncMock(return_value=select_row)),
            MagicMock(rowcount=1),
        ]
    )

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, s3_backend=s3_backend)

    await service.delete_attachment("atc_del", "ws1")

    s3_delete.assert_awaited_once_with("attachments/ws1/atc_del")


@pytest.mark.asyncio
async def test_delete_attachment_skips_s3_for_postgres_row() -> None:
    """delete_attachment must NOT call S3 when blob_backend='postgres'."""
    s3_backend, _ = _make_s3_backend()
    s3_delete = AsyncMock()
    s3_backend.delete = s3_delete  # type: ignore[method-assign]

    select_row = _FakeRow(blob_backend="postgres", blob_key="atc_pg")
    conn, _ = _make_conn([select_row])
    conn.execute = AsyncMock(
        side_effect=[
            MagicMock(fetchone=AsyncMock(return_value=select_row)),
            MagicMock(rowcount=1),
        ]
    )

    @asynccontextmanager
    async def _factory():
        yield conn

    lock = asyncio.Lock()
    service = AttachmentService(_factory, lock, s3_backend=s3_backend)

    await service.delete_attachment("atc_pg", "ws1")

    s3_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _ScopedUploader backend selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scoped_uploader_honors_configured_blob_backend() -> None:
    """Workflow-generated uploads must use the service's delegated backend."""
    service = MagicMock()
    service.blob_backend = "s3"
    service.save_attachment = AsyncMock(return_value=("atc_workflow", None))
    scope = build_attachment_scope(
        workspace_id="ws1", workflow_id="wf1", thread_id="thread1"
    )

    uploader = _ScopedUploader(service, scope)  # type: ignore[arg-type]

    attachment_id, download_url = await uploader.upload_attachment(
        b"generated", "result.txt", "text/plain"
    )

    assert attachment_id == "atc_workflow"
    assert download_url.endswith("/api/chatkit/attachments/atc_workflow")
    service.save_attachment.assert_awaited_once()
    assert service.save_attachment.await_args.kwargs["blob_backend"] == "s3"
