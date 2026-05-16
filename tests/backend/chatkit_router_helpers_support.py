"""Shared utilities and fixtures for ChatKit router helper tests."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
import pytest
from starlette.requests import Request
from orcheo_backend.app.routers import chatkit


async def _empty_receive() -> dict[str, Any]:
    """Provide a no-op HTTP receive coroutine for ASGI Request objects."""
    return {"type": "http.request", "body": b"", "more_body": False}


def make_chatkit_request(
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> Request:
    """Construct a Starlette Request with optional headers/cookies."""
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    if cookies:
        cookie_value = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chatkit",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope, _empty_receive)


@pytest.fixture
def reset_chatkit_limiters() -> None:
    """Reset chatkit rate limiters once for dependent fixtures."""
    chatkit._reset_rate_limiters()
    chatkit._get_rate_limiters().ip.reset()
    chatkit._get_rate_limiters().jwt.reset()
    chatkit._get_rate_limiters().workflow.reset()
    chatkit._get_rate_limiters().session.reset()


__all__ = [
    "make_chatkit_request",
    "reset_chatkit_limiters",
]
