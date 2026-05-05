"""API client request method tests for the CLI."""

from __future__ import annotations
import json
import httpx
import pytest
import respx
from orcheo_sdk.cli.errors import APICallError
from orcheo_sdk.cli.http import ApiClient


def test_api_client_get_success() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        result = client.get("/api/test")
    assert result == {"key": "value"}


def test_api_client_get_with_params() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        route = respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        client.get("/api/test", params={"foo": "bar"})
    assert route.calls[0].request.url.params.get("foo") == "bar"


def test_api_client_get_http_error() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        with pytest.raises(APICallError) as exc_info:
            client.get("/api/test")
    assert exc_info.value.status_code == 404


def test_api_client_get_request_error() -> None:
    client = ApiClient(base_url="http://nonexistent.invalid.test", token="token123")
    with pytest.raises(APICallError):
        client.get("/api/test")


def test_api_client_post_success() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.post("http://test.com/api/test").mock(
            return_value=httpx.Response(201, json={"id": "123"})
        )
        result = client.post("/api/test", json_body={"key": "value"})
    assert result == {"id": "123"}


def test_api_client_post_no_content() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.post("http://test.com/api/test").mock(return_value=httpx.Response(204))
        result = client.post("/api/test", json_body={})
    assert result is None


def test_api_client_put_success() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.put("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"updated": True})
        )
        result = client.put("/api/test", json_body={"value": 1})
    assert result == {"updated": True}


def test_api_client_put_no_content() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.put("http://test.com/api/test").mock(return_value=httpx.Response(204))
        result = client.put("/api/test", json_body={"value": 1})
    assert result is None


def test_api_client_post_http_error() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.post("http://test.com/api/test").mock(
            return_value=httpx.Response(
                400, json={"detail": {"message": "Bad request"}}
            )
        )
        with pytest.raises(APICallError) as exc_info:
            client.post("/api/test", json_body={})
    assert exc_info.value.status_code == 400


def test_api_client_post_request_error() -> None:
    client = ApiClient(base_url="http://nonexistent.invalid.test", token="token123")
    with pytest.raises(APICallError):
        client.post("/api/test", json_body={})


def test_api_client_delete_success() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.delete("http://test.com/api/test/123").mock(
            return_value=httpx.Response(204)
        )
        client.delete("/api/test/123")


def test_api_client_delete_http_error() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        respx.delete("http://test.com/api/test/123").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )
        with pytest.raises(APICallError) as exc_info:
            client.delete("/api/test/123")
    assert exc_info.value.status_code == 404


def test_api_client_delete_request_error() -> None:
    client = ApiClient(base_url="http://nonexistent.invalid.test", token="token123")
    with pytest.raises(APICallError):
        client.delete("/api/test/123")


def test_api_client_base_url_property() -> None:
    client = ApiClient(base_url="http://test.com/", token="token123")
    assert client.base_url == "http://test.com"


def test_api_client_without_token() -> None:
    """Test that ApiClient works without a token."""
    client = ApiClient(base_url="http://test.com", token=None)
    with respx.mock:
        route = respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        result = client.get("/api/test")
    assert result == {"key": "value"}
    # Verify no Authorization header was sent
    assert "Authorization" not in route.calls[0].request.headers


def test_api_client_token_provider_overrides_static_token() -> None:
    provided_tokens: list[str | None] = ["provider-token"]

    def token_provider() -> str | None:
        return provided_tokens[0]

    client = ApiClient(
        base_url="http://test.com",
        token="static-token",
        token_provider=token_provider,
    )
    with respx.mock:
        route = respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        client.get("/api/test")
    assert route.calls[0].request.headers["Authorization"] == "Bearer provider-token"


def test_api_client_token_provider_falls_back_to_static_token() -> None:
    def token_provider() -> str | None:
        return None

    client = ApiClient(
        base_url="http://test.com",
        token="static-token",
        token_provider=token_provider,
    )
    with respx.mock:
        route = respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        client.get("/api/test")
    assert route.calls[0].request.headers["Authorization"] == "Bearer static-token"


def test_api_client_delete_with_json_body() -> None:
    client = ApiClient(base_url="http://test.com", token="token123")
    with respx.mock:
        route = respx.delete("http://test.com/api/test/123").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )
        result = client.delete("/api/test/123", json_body={"foo": "bar"})
    assert result == {"deleted": True}
    body = json.loads(route.calls[0].request.content)
    assert body == {"foo": "bar"}


def test_api_client_get_active_token_returns_token() -> None:
    """Test that get_active_token exposes the active bearer token."""
    client = ApiClient(base_url="http://test.com", token="static-token")
    assert client.get_active_token() == "static-token"


def test_api_client_uses_tenant_header_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that the CLI sends the active tenant header when configured."""
    monkeypatch.setenv("ORCHEO_TENANT", "tenant-1")
    client = ApiClient(base_url="http://test.com", token=None)
    with respx.mock:
        route = respx.get("http://test.com/api/test").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        client.get("/api/test")
    assert route.calls[0].request.headers["X-Orcheo-Tenant"] == "tenant-1"
