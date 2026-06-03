"""Tests for the ChatKit upload endpoint (blob-backed, scoped auth)."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.backend.api.shared import backend_app
from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
    AttachmentNotFoundError,
)


class _FakeUploadFile:
    def __init__(self, filename: str | None, content_type: str | None, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self, size: int = -1) -> bytes:
        return self._content


class _NonDecodableBytes(bytes):
    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:  # type: ignore[override]
        raise UnicodeDecodeError(encoding, b"\x80", 0, 1, "forced failure")


def _make_auth_result(
    workflow_id: Any = None,
    auth_mode: str = "publish",
    workspace_id: str = "ws-test",
) -> backend_app.routers.chatkit.ChatKitAuthResult:
    return backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=workflow_id or uuid4(),
        actor="workflow:test",
        auth_mode=auth_mode,
        subject=None,
        workspace_id=workspace_id,
    )


def _make_server_with_service(
    attachment_id: str = "atc_testid",
    minted_session: str | None = None,
) -> MagicMock:
    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=(attachment_id, minted_session))
    store = MagicMock()
    store.attachment_service = service
    server = MagicMock()
    server.store = store
    return server


# ---------------------------------------------------------------------------
# Validation tests (no auth needed — rejected before auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_upload_requires_workflow_id(api_client: TestClient) -> None:
    """Upload without workflow_id should return 400."""
    response = api_client.post(
        "/api/chatkit/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "chatkit.upload.workflow_id_missing"


@pytest.mark.asyncio
async def test_chatkit_upload_enforces_size_limit(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uploads larger than the configured limit should return 413."""
    from orcheo.config import get_settings

    workflow_id = str(uuid4())
    auth_result = _make_auth_result()
    server = _make_server_with_service()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )
    monkeypatch.setenv("ORCHEO_CHATKIT_MAX_UPLOAD_SIZE_BYTES", "4")
    get_settings(refresh=True)

    response = api_client.post(
        "/api/chatkit/upload",
        data={"workflow_id": workflow_id},
        files={"file": ("note.txt", b"too big", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "chatkit.upload.too_large"
    server.store.attachment_service.save_attachment.assert_not_called()

    monkeypatch.delenv("ORCHEO_CHATKIT_MAX_UPLOAD_SIZE_BYTES", raising=False)
    get_settings(refresh=True)


@pytest.mark.asyncio
async def test_chatkit_upload_sanitizes_filename(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filename traversal components should be stripped before storage."""
    workflow_id = str(uuid4())
    server = _make_server_with_service(attachment_id="atc_sanitized")
    auth_result = _make_auth_result()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        "/api/chatkit/upload",
        data={"workflow_id": workflow_id},
        files={"file": ("../../../../etc/passwd", b"payload", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "passwd"
    assert "storage_path" not in payload


# ---------------------------------------------------------------------------
# workflow_id via query param (public-chat-widget direct-upload strategy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_upload_accepts_workflow_id_as_query_param(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workflow_id sent as a URL query param (not form field) is accepted."""
    workflow_id = str(uuid4())
    server = _make_server_with_service(attachment_id="atc_qp")
    auth_result = _make_auth_result()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        f"/api/chatkit/upload?workflow_id={workflow_id}",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "atc_qp"


@pytest.mark.asyncio
async def test_chatkit_upload_form_workflow_id_takes_precedence(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Form field workflow_id is used even when query param is also present."""
    workflow_id = str(uuid4())
    server = _make_server_with_service(attachment_id="atc_form")
    auth_result = _make_auth_result()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        f"/api/chatkit/upload?workflow_id=qp_{workflow_id}",
        data={"workflow_id": workflow_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    call_kwargs = server.store.attachment_service.save_attachment.call_args.kwargs
    # The form field value (workflow_id) was used — not the query param
    assert call_kwargs["workflow_id"] == str(auth_result.workflow_id)


# ---------------------------------------------------------------------------
# Success path — opaque metadata returned, no filesystem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_upload_returns_opaque_metadata(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful upload returns attachment id and metadata without storage_path."""
    workflow_id = str(uuid4())
    server = _make_server_with_service(attachment_id="atc_abc123")
    auth_result = _make_auth_result()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        "/api/chatkit/upload",
        data={"workflow_id": workflow_id},
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "atc_abc123"
    assert payload["name"] == "notes.txt"
    assert payload["mime_type"] == "text/plain"
    assert payload["type"] == "file"
    assert payload["size"] == 11
    assert "storage_path" not in payload
    assert "upload_session_id" not in payload


@pytest.mark.asyncio
async def test_chatkit_upload_includes_minted_upload_session_id(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When backend mints an upload_session_id, it is returned to the client."""
    workflow_id = str(uuid4())
    server = _make_server_with_service(
        attachment_id="atc_xyz", minted_session="ups_minted99"
    )
    auth_result = _make_auth_result()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        "/api/chatkit/upload",
        data={"workflow_id": workflow_id},
        files={"file": ("doc.txt", b"data", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["upload_session_id"] == "ups_minted99"


@pytest.mark.asyncio
async def test_chatkit_upload_passes_thread_and_session_ids(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """thread_id and upload_session_id form fields are forwarded to the service."""
    workflow_id = str(uuid4())
    server = _make_server_with_service()
    auth_result = _make_auth_result(workspace_id="ws-abc")

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = api_client.post(
        "/api/chatkit/upload",
        data={
            "workflow_id": workflow_id,
            "thread_id": "thread-123",
            "upload_session_id": "ups-client-456",
        },
        files={"file": ("doc.txt", b"data", "text/plain")},
    )

    assert response.status_code == 200
    call_kwargs = server.store.attachment_service.save_attachment.call_args.kwargs
    assert call_kwargs["thread_id"] == "thread-123"
    assert call_kwargs["upload_session_id"] == "ups-client-456"
    assert call_kwargs["workspace_id"] == "ws-abc"


# ---------------------------------------------------------------------------
# Auth rejection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_upload_rejects_invalid_encoding(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files with non-text encoding should be rejected with 400."""
    workflow_id = str(uuid4())
    auth_result = _make_auth_result()
    server = _make_server_with_service()

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    file = _FakeUploadFile("text.txt", "text/plain", _NonDecodableBytes(b"data"))
    request = SimpleNamespace(query_params={})

    with pytest.raises(Exception) as excinfo:
        await backend_app.routers.chatkit.upload_chatkit_file(
            file=file,
            request=request,
            repository=MagicMock(),
            workflow_id=workflow_id,
        )

    assert getattr(excinfo.value, "status_code", None) == 400
    assert excinfo.value.detail["code"] == "chatkit.upload.invalid_encoding"


@pytest.mark.asyncio
async def test_chatkit_upload_awaits_store_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload should await the store initialization hook when present."""

    workflow_id = str(uuid4())
    auth_result = _make_auth_result()
    server = _make_server_with_service(attachment_id="atc_init")
    server.store._ensure_initialized = AsyncMock(return_value=None)

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = await backend_app.routers.chatkit.upload_chatkit_file(
        file=_FakeUploadFile("notes.txt", "text/plain", b"hello"),
        request=SimpleNamespace(query_params={}),
        repository=MagicMock(),
        workflow_id=workflow_id,
        thread_id="thread-1",
        upload_session_id="ups-1",
    )

    assert response.status_code == 200
    assert server.store._ensure_initialized.await_count == 1


@pytest.mark.asyncio
async def test_chatkit_upload_skips_store_initialization_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload should continue when the store has no init hook."""

    workflow_id = str(uuid4())
    auth_result = _make_auth_result()
    server = _make_server_with_service(attachment_id="atc_noinit")
    server.store._ensure_initialized = None

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = await backend_app.routers.chatkit.upload_chatkit_file(
        file=_FakeUploadFile("notes.txt", "text/plain", b"hello"),
        request=SimpleNamespace(query_params={}),
        repository=MagicMock(),
        workflow_id=workflow_id,
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chatkit_upload_surfaces_processing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected upload failures should be converted into a 500 response."""

    workflow_id = str(uuid4())
    auth_result = _make_auth_result()
    server = _make_server_with_service(attachment_id="atc_error")
    server.store._ensure_initialized = AsyncMock(return_value=None)
    server.store.attachment_service.save_attachment = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    with pytest.raises(Exception) as excinfo:
        await backend_app.routers.chatkit.upload_chatkit_file(
            file=_FakeUploadFile("notes.txt", "text/plain", b"hello"),
            request=SimpleNamespace(query_params={}),
            repository=MagicMock(),
            workflow_id=workflow_id,
        )

    assert getattr(excinfo.value, "status_code", None) == 500
    assert excinfo.value.detail["code"] == "chatkit.upload.processing_error"


def test_sanitize_filename_none_returns_default() -> None:
    """A missing filename should normalize to the upload default."""

    assert backend_app.routers.chatkit._sanitize_filename(None) == "uploaded_file"


@pytest.mark.asyncio
async def test_chatkit_upload_rejects_unauthenticated_for_private_workflow(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload to a private workflow should be rejected with 403."""
    from fastapi import HTTPException, status

    workflow_id = str(uuid4())

    async def _raise_403(*, request: Any, payload: Any, repository: Any) -> Any:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "workflow is not published",
                "code": "chatkit.auth.not_published",
            },
        )

    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        _raise_403,
    )

    response = api_client.post(
        "/api/chatkit/upload",
        data={"workflow_id": workflow_id},
        files={"file": ("doc.txt", b"content", "text/plain")},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chatkit_download_attachment_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment download returns 503 when storage is unavailable."""

    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = None
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    with pytest.raises(Exception) as excinfo:
        await backend_app.routers.chatkit.download_chatkit_attachment(
            attachment_id="atc_123",
            request=SimpleNamespace(),
        )

    assert getattr(excinfo.value, "status_code", None) == 503
    assert excinfo.value.detail["code"] == "chatkit.attachments.unavailable"


@pytest.mark.asyncio
async def test_chatkit_download_attachment_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment download returns 404 when the attachment is missing."""

    service = MagicMock()
    service.load_attachment_bytes_public = AsyncMock(
        side_effect=AttachmentNotFoundError("missing")
    )
    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = service
    server.store._ensure_initialized = AsyncMock(return_value=None)
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    with pytest.raises(Exception) as excinfo:
        await backend_app.routers.chatkit.download_chatkit_attachment(
            attachment_id="atc_123",
            request=SimpleNamespace(),
        )

    assert getattr(excinfo.value, "status_code", None) == 404
    assert excinfo.value.detail["code"] == "chatkit.attachments.not_found"


@pytest.mark.asyncio
async def test_chatkit_download_attachment_returns_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful attachment download returns the stored bytes and headers."""

    payload = SimpleNamespace(
        name="../report.txt",
        mime_type="text/plain",
        content=b"hello world",
    )
    service = MagicMock()
    service.load_attachment_bytes_public = AsyncMock(return_value=payload)
    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = service
    server.store._ensure_initialized = AsyncMock(return_value=None)
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = await backend_app.routers.chatkit.download_chatkit_attachment(
        attachment_id="atc_123",
        request=SimpleNamespace(),
    )

    assert response.status_code == 200
    assert response.body == b"hello world"
    assert response.headers["content-disposition"] == (
        'attachment; filename="report.txt"'
    )


@pytest.mark.asyncio
async def test_chatkit_download_attachment_skips_store_initialization_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download should continue when the store has no init hook."""

    payload = SimpleNamespace(
        name="report.txt",
        mime_type="text/plain",
        content=b"hello world",
    )
    service = MagicMock()
    service.load_attachment_bytes_public = AsyncMock(return_value=payload)
    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = service
    server.store._ensure_initialized = None
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = await backend_app.routers.chatkit.download_chatkit_attachment(
        attachment_id="atc_123",
        request=SimpleNamespace(),
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chatkit_download_attachment_handles_sync_init_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download should accept a synchronous init hook that returns nothing."""

    payload = SimpleNamespace(
        name="report.txt",
        mime_type="text/plain",
        content=b"hello world",
    )
    service = MagicMock()
    service.load_attachment_bytes_public = AsyncMock(return_value=payload)
    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = service
    server.store._ensure_initialized = MagicMock(return_value=None)
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    response = await backend_app.routers.chatkit.download_chatkit_attachment(
        attachment_id="atc_123",
        request=SimpleNamespace(),
    )

    assert response.status_code == 200
    server.store._ensure_initialized.assert_called_once()


@pytest.mark.asyncio
async def test_chatkit_download_attachment_surfaces_processing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download errors other than not-found become a 500."""

    service = MagicMock()
    service.load_attachment_bytes_public = AsyncMock(side_effect=RuntimeError("boom"))
    server = MagicMock()
    server.store = MagicMock()
    server.store.attachment_service = service
    server.store._ensure_initialized = None
    monkeypatch.setattr(
        backend_app.routers.chatkit, "_resolve_chatkit_server", lambda: server
    )

    with pytest.raises(Exception) as excinfo:
        await backend_app.routers.chatkit.download_chatkit_attachment(
            attachment_id="atc_123",
            request=SimpleNamespace(),
        )

    assert getattr(excinfo.value, "status_code", None) == 500
    assert excinfo.value.detail["code"] == "chatkit.attachments.load_error"
