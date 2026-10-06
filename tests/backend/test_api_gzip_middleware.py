"""Tests for the API-scoped gzip middleware."""

from __future__ import annotations
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from orcheo_backend.app.factory import _ApiGZipMiddleware


def _large_body(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("x" * 4096)


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/api/executions/run/trace", _large_body),
            Route("/api/mcp/messages", _large_body),
            Route("/internal/hosted-apps/asset", _large_body),
        ]
    )
    app.add_middleware(_ApiGZipMiddleware)
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "compressed"),
    [
        ("/api/executions/run/trace", True),
        ("/api/mcp/messages", False),
        ("/internal/hosted-apps/asset", False),
    ],
)
def test_gzip_is_scoped_to_rest_api(
    client: TestClient, path: str, compressed: bool
) -> None:
    response = client.get(path, headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert (response.headers.get("content-encoding") == "gzip") is compressed
    assert response.text == "x" * 4096
