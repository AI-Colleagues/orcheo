"""Tests for browser context HTTP server endpoints."""

from __future__ import annotations
import threading
from datetime import UTC, datetime
from http.server import HTTPServer
from unittest.mock import patch
import httpx
import pytest
from orcheo_sdk.cli.browser_context.server import (
    _cors_headers,
    _resolve_timestamp,
    create_request_handler,
    run_server,
)
from orcheo_sdk.cli.browser_context.store import BrowserContextStore


def _ts() -> str:
    """Return a fresh ISO 8601 timestamp string."""
    return datetime.now(UTC).isoformat()


@pytest.fixture()
def context_server() -> tuple[str, HTTPServer]:
    """Start a context server on a random port and return (base_url, server)."""
    store = BrowserContextStore()
    handler = create_request_handler(store)
    server = HTTPServer(("localhost", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{port}", server
    server.shutdown()


def test_get_context_empty(context_server: tuple[str, HTTPServer]) -> None:
    """GET /context on empty store returns null context."""
    base_url, _ = context_server
    resp = httpx.get(f"{base_url}/context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] is None
    assert data["total_sessions"] == 0


def test_post_then_get_context(context_server: tuple[str, HTTPServer]) -> None:
    """POST /context then GET /context returns the posted context."""
    base_url, _ = context_server
    payload = {
        "session_id": "tab-1",
        "page": "workflow",
        "workflow_id": "wf-abc",
        "workflow_name": "Test Flow",
        "focused": True,
        "timestamp": _ts(),
    }
    resp = httpx.post(f"{base_url}/context", json=payload)
    assert resp.status_code == 204

    resp = httpx.get(f"{base_url}/context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "tab-1"
    assert data["page"] == "workflow"
    assert data["workflow_id"] == "wf-abc"
    assert data["total_sessions"] == 1


def test_get_sessions(context_server: tuple[str, HTTPServer]) -> None:
    """GET /context/sessions returns all sessions."""
    base_url, _ = context_server
    for i in range(3):
        httpx.post(
            f"{base_url}/context",
            json={
                "session_id": f"tab-{i}",
                "page": "gallery",
                "workflow_id": None,
                "workflow_name": None,
                "focused": i == 0,
                "timestamp": _ts(),
            },
        )

    resp = httpx.get(f"{base_url}/context/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 3


def test_cors_headers_allow_all(context_server: tuple[str, HTTPServer]) -> None:
    """All responses include Access-Control-Allow-Origin: *."""
    base_url, _ = context_server
    resp = httpx.get(f"{base_url}/context")
    assert resp.headers["access-control-allow-origin"] == "*"


def test_options_preflight(context_server: tuple[str, HTTPServer]) -> None:
    """OPTIONS request returns 204 with CORS headers."""
    base_url, _ = context_server
    resp = httpx.options(f"{base_url}/context")
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-methods" in resp.headers


def test_post_missing_fields(context_server: tuple[str, HTTPServer]) -> None:
    """POST /context with missing required fields returns 400."""
    base_url, _ = context_server
    resp = httpx.post(f"{base_url}/context", json={"session_id": "x"})
    assert resp.status_code == 400
    data = resp.json()
    assert "missing fields" in data["error"]


def test_post_invalid_json(context_server: tuple[str, HTTPServer]) -> None:
    """POST /context with invalid JSON returns 400."""
    base_url, _ = context_server
    resp = httpx.post(
        f"{base_url}/context",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_post_empty_body(context_server: tuple[str, HTTPServer]) -> None:
    """POST /context with empty body returns 400."""
    base_url, _ = context_server
    resp = httpx.post(
        f"{base_url}/context",
        content=b"",
        headers={"Content-Type": "application/json", "Content-Length": "0"},
    )
    assert resp.status_code == 400


def test_unknown_path(context_server: tuple[str, HTTPServer]) -> None:
    """GET on unknown path returns 404."""
    base_url, _ = context_server
    resp = httpx.get(f"{base_url}/unknown")
    assert resp.status_code == 404


def test_post_unknown_path(context_server: tuple[str, HTTPServer]) -> None:
    """POST on unknown path returns 404."""
    base_url, _ = context_server
    resp = httpx.post(f"{base_url}/unknown", json={})
    assert resp.status_code == 404


def test_cors_headers_returns_wildcard() -> None:
    """_cors_headers always returns Access-Control-Allow-Origin: *."""
    headers = _cors_headers()
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Access-Control-Allow-Methods" in headers
    assert "Access-Control-Allow-Headers" in headers


def test_resolve_timestamp_invalid() -> None:
    """_resolve_timestamp falls back to now() when timestamp is unparseable."""
    before = datetime.now(UTC)
    result = _resolve_timestamp({"timestamp": "not-a-date"})
    after = datetime.now(UTC)
    assert before <= result <= after


def test_resolve_timestamp_no_key() -> None:
    """_resolve_timestamp falls back to now() when timestamp key is absent."""
    before = datetime.now(UTC)
    result = _resolve_timestamp({})
    after = datetime.now(UTC)
    assert before <= result <= after


def test_run_server_keyboard_interrupt() -> None:
    """run_server shuts down cleanly on KeyboardInterrupt."""

    class _FakeServer:
        server_address = ("localhost", 9876)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    with patch(
        "orcheo_sdk.cli.browser_context.server.HTTPServer",
        return_value=_FakeServer(),
    ):
        # Should not raise
        run_server(host="localhost", port=9876)


def test_run_server_port_retry() -> None:
    """run_server retries the next port when the first is unavailable."""
    call_count = 0

    class _FakeServer:
        server_address = ("localhost", 9878)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def _fake_http_server(addr: tuple[str, int], handler: type) -> _FakeServer:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("port in use")
        return _FakeServer()

    with patch(
        "orcheo_sdk.cli.browser_context.server.HTTPServer",
        side_effect=_fake_http_server,
    ):
        run_server(host="localhost", port=9877, max_port_attempts=3)

    assert call_count == 2


def test_run_server_all_ports_busy() -> None:
    """run_server raises OSError when all port attempts fail."""
    with patch(
        "orcheo_sdk.cli.browser_context.server.HTTPServer",
        side_effect=OSError("port in use"),
    ):
        with pytest.raises(OSError, match="Could not bind"):
            run_server(host="localhost", port=9900, max_port_attempts=3)
