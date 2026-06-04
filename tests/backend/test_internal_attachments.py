"""Tests for the internal sandbox attachment upload endpoint."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orcheo.sandbox.broker import (
    BrokerScopeError,
    BrokerTokenInvalidError,
    CredentialBroker,
)
from orcheo_backend.app.internal_attachments import (
    _get_attachment_service,
    _resolve_download_base_url,
    build_internal_attachment_router,
)

_PATCH_SERVICE = "orcheo_backend.app.internal_attachments._get_attachment_service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_broker(workspace_id: str = "ws-1") -> tuple[CredentialBroker, str]:
    """Return a broker and a valid token string for workspace_id."""
    broker = CredentialBroker(
        secret=b"test-secret",
        resolver=lambda ws, name: "resolved",
        ttl_seconds=3600,
    )
    token = broker.issue(workspace_id=workspace_id, run_id="run-1")
    return broker, token


def _make_app(broker: CredentialBroker) -> FastAPI:
    app = FastAPI()
    app.include_router(build_internal_attachment_router(broker))
    return app


# ---------------------------------------------------------------------------
# _resolve_download_base_url (lines 25-28)
# ---------------------------------------------------------------------------


def test_resolve_download_base_url_uses_orcheo_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefers ORCHEO_API_URL when set (lines 25-26)."""
    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com/")
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)

    result = _resolve_download_base_url()

    assert result == "https://api.example.com"


def test_resolve_download_base_url_falls_back_to_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to ORCHEO_API_BASE_URL when ORCHEO_API_URL is unset (lines 27-28)."""
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.setenv("ORCHEO_API_BASE_URL", "https://base.example.com/")

    result = _resolve_download_base_url()

    assert result == "https://base.example.com"


def test_resolve_download_base_url_returns_empty_when_neither_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns empty string when no env var is set."""
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)

    result = _resolve_download_base_url()

    assert result == ""


# ---------------------------------------------------------------------------
# _get_attachment_service (lines 33-41)
# ---------------------------------------------------------------------------


def test_get_attachment_service_is_callable() -> None:
    """_get_attachment_service is importable and returns None or a service (lines 33-41)."""
    result = _get_attachment_service()
    assert result is None or hasattr(result, "save_attachment")


def test_get_attachment_service_returns_none_when_server_is_none() -> None:
    """Returns None when get_chatkit_server() returns None (line 38)."""
    import sys

    # The module is already loaded; patch get_chatkit_server in orcheo_backend.app
    app_module = sys.modules.get("orcheo_backend.app")
    if app_module is not None:
        original = getattr(app_module, "get_chatkit_server", None)
        try:
            app_module.get_chatkit_server = lambda: None  # type: ignore[attr-defined]
            result = _get_attachment_service()
            assert result is None
        finally:
            if original is not None:
                app_module.get_chatkit_server = original  # type: ignore[attr-defined]


def test_get_attachment_service_returns_none_on_exception() -> None:
    """Returns None when get_chatkit_server raises any Exception (lines 40-41)."""
    import sys

    def _raise() -> None:
        raise RuntimeError("server down")

    app_module = sys.modules.get("orcheo_backend.app")
    if app_module is not None:
        original = getattr(app_module, "get_chatkit_server", None)
        try:
            app_module.get_chatkit_server = _raise  # type: ignore[attr-defined]
            result = _get_attachment_service()
            assert result is None
        finally:
            if original is not None:
                app_module.get_chatkit_server = original  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# build_internal_attachment_router — /upload endpoint (lines 58-133)
# ---------------------------------------------------------------------------


def test_upload_returns_401_when_no_authorization() -> None:
    """Missing Authorization header → 401 (lines 58-62)."""
    broker, _ = _make_broker()
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    response = client.post(
        "/internal/attachments/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 401


def test_upload_returns_401_when_authorization_not_bearer() -> None:
    """Non-bearer Authorization header → 401 (lines 58-62)."""
    broker, _ = _make_broker()
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    response = client.post(
        "/internal/attachments/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401


def test_upload_returns_401_when_invalid_token() -> None:
    """BrokerTokenInvalidError → 401 (lines 66-69)."""
    broker = MagicMock()
    broker.parse.side_effect = BrokerTokenInvalidError("bad token")
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    response = client.post(
        "/internal/attachments/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer bad-token"},
    )
    assert response.status_code == 401


def test_upload_returns_403_when_scope_error() -> None:
    """BrokerScopeError → 403 (lines 70-73)."""
    broker = MagicMock()
    broker.parse.side_effect = BrokerScopeError("wrong scope")
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    response = client.post(
        "/internal/attachments/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers={"Authorization": "Bearer some-token"},
    )
    assert response.status_code == 403


def test_upload_returns_403_on_cross_workspace() -> None:
    """Cross-workspace header mismatch → 403 (lines 76-80)."""
    broker, token = _make_broker(workspace_id="ws-correct")
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    response = client.post(
        "/internal/attachments/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Orcheo-Workspace": "ws-wrong",  # mismatch
        },
    )
    assert response.status_code == 403
    assert "Cross-workspace" in response.json()["detail"]


def test_upload_returns_503_when_service_unavailable() -> None:
    """No attachment service → 503 (lines 83-87)."""
    broker, token = _make_broker()
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=None):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_upload_returns_500_when_save_fails() -> None:
    """Service save failure → 500 (lines 107-119)."""
    broker, token = _make_broker()
    service = MagicMock()
    service.save_attachment = AsyncMock(side_effect=RuntimeError("disk full"))
    service.blob_backend = "postgres"
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=service):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    assert "Failed to store" in response.json()["detail"]


def test_upload_succeeds_and_returns_attachment_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful upload returns attachment_id and download_url (lines 121-136)."""
    broker, token = _make_broker()
    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_test123", "ups_session"))
    service.blob_backend = "postgres"
    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com")
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=service):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("report.csv", b"col1,col2\n1,2\n", "text/csv")},
            data={"workflow_id": "wf-1", "thread_id": "thr-1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "atc_test123"
    assert "atc_test123" in body["download_url"]


def test_upload_uses_provided_filename() -> None:
    """Provided filename is used in save_attachment call (line 90)."""
    broker, token = _make_broker()
    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_file", None))
    service.blob_backend = "postgres"
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=service):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("data.bin", b"data", "application/octet-stream")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    call_kwargs = service.save_attachment.await_args.kwargs
    assert call_kwargs["name"] == "data.bin"


def test_upload_accepts_matching_workspace_header() -> None:
    """Matching X-Orcheo-Workspace header is accepted (line 76 True → no 403)."""
    broker, token = _make_broker(workspace_id="ws-match")
    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_ok", None))
    service.blob_backend = "postgres"
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=service):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("file.txt", b"content", "text/plain")},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Orcheo-Workspace": "ws-match",  # matches token workspace_id
            },
        )

    assert response.status_code == 200


def test_upload_with_upload_session_id_form_field() -> None:
    """upload_session_id form field is forwarded to save_attachment."""
    broker, token = _make_broker()
    service = MagicMock()
    service.save_attachment = AsyncMock(return_value=("atc_session", "ups_1"))
    service.blob_backend = "postgres"
    client = TestClient(_make_app(broker), raise_server_exceptions=False)

    with patch(_PATCH_SERVICE, return_value=service):
        response = client.post(
            "/internal/attachments/upload",
            files={"file": ("results.csv", b"a,b\n1,2\n", "text/csv")},
            data={"upload_session_id": "ups_provided"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    call_kwargs = service.save_attachment.await_args.kwargs
    assert call_kwargs["upload_session_id"] == "ups_provided"
