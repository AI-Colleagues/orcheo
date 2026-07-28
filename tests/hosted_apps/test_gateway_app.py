"""ASGI delivery tests for manifest-only Hosted Apps serving."""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from orcheo_app_gateway.main import create_app


def _gateway(tmp_path: Path, monkeypatch) -> tuple[TestClient, str]:
    """Build one public local descriptor and immutable deployment."""
    deployment_id = uuid4()
    root = tmp_path / "bundles"
    deployment = root / "deployments" / str(deployment_id)
    deployment.mkdir(parents=True)
    (deployment / "index.html").write_text("<script>ok()</script><h1>Portal</h1>")
    (deployment / "main.js").write_text("console.log('ok')")
    manifest = {
        "files": {
            "index.html": {"content_type": "text/html; charset=utf-8"},
            "main.js": {"content_type": "text/javascript; charset=utf-8"},
        },
        "html_policy": {"index.html": {"inline_script_hashes": ["sha256-example"]}},
    }
    (deployment / "__manifest__.json").write_text(json.dumps(manifest))
    descriptors = tmp_path / "descriptors.json"
    descriptors.write_text(
        json.dumps(
            {
                "portal": {
                    "deployment_id": str(deployment_id),
                    "visibility": "public",
                }
            }
        )
    )
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(root))
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_DESCRIPTOR_FILE", str(descriptors))
    return TestClient(create_app()), str(deployment_id)


def test_gateway_serves_only_manifest_assets_with_platform_headers(
    tmp_path: Path, monkeypatch
) -> None:
    """Stable alias delivery applies CSP hashes and service-worker denial."""
    client, _deployment_id = _gateway(tmp_path, monkeypatch)
    response = client.get("/", headers={"host": "portal.apps.test"})
    assert response.status_code == 200
    assert "worker-src 'none'" in response.headers["content-security-policy"]
    assert "'sha256-example'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-cache"


def test_gateway_serves_postgres_backed_assets_through_backend(
    tmp_path: Path, monkeypatch
) -> None:
    """Stack delivery reads durable assets through the gateway-only backend lane."""
    deployment_id = uuid4()
    manifest = {
        "files": {
            "index.html": {
                "content_type": "text/html; charset=utf-8",
                "sha256": "a" * 64,
            }
        },
        "html_policy": {"index.html": {"inline_script_hashes": []}},
    }
    requested_urls: list[str] = []
    streamed_requests: list[bool] = []

    class BackendStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<h1>Database "
            yield b"app</h1>"

    class BackendClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            requested_urls.append(url)
            if url.endswith("/internal/hosted-apps/resolve"):
                return httpx.Response(
                    200,
                    json={
                        "deployment_id": str(deployment_id),
                        "visibility": "public",
                    },
                )
            if url.endswith("/assets/__manifest__.json"):
                return httpx.Response(200, content=json.dumps(manifest).encode())
            if url.endswith("/assets/index.html"):
                return httpx.Response(200, content=b"<h1>Database app</h1>")
            return httpx.Response(404)

        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url, **kwargs)

        async def send(self, request, *, stream):
            requested_urls.append(str(request.url))
            streamed_requests.append(stream)
            if str(request.url).endswith("/assets/index.html"):
                return httpx.Response(
                    200,
                    stream=BackendStream(),
                    request=request,
                )
            return httpx.Response(404, request=request)

        async def aclose(self):
            return None

    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_BACKEND_URL", "http://backend")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_SECRET", "g" * 64)
    monkeypatch.setattr(
        "orcheo_app_gateway.main.httpx.AsyncClient",
        lambda **_kwargs: BackendClient(),
    )

    response = TestClient(create_app()).get("/", headers={"host": "portal.apps.test"})

    assert response.status_code == 200
    assert response.text == "<h1>Database app</h1>"
    assert any(url.endswith("/assets/__manifest__.json") for url in requested_urls)
    assert any(url.endswith("/assets/index.html") for url in requested_urls)
    assert streamed_requests == [True]


def test_gateway_spa_fallback_does_not_mask_unsafe_or_reserved_paths(
    tmp_path: Path, monkeypatch
) -> None:
    """Unknown safe navigation falls back, while runtime/traversal paths do not."""
    client, _deployment_id = _gateway(tmp_path, monkeypatch)
    assert (
        client.get("/dashboard", headers={"host": "portal.apps.test"}).status_code
        == 200
    )
    config = client.get("/__orcheo/config", headers={"host": "portal.apps.test"})
    assert config.status_code == 200
    assert config.headers["cache-control"] == "private, no-store"
    assert config.json() == {"authenticated": False, "visibility": "public"}
    assert client.get(
        "/%2e%2e/secret", headers={"host": "portal.apps.test"}
    ).status_code in {400, 404}


def test_runtime_route_rejects_browser_identity_and_origin_bypass(
    tmp_path: Path, monkeypatch
) -> None:
    """Browser requests cannot inject Orcheo identity or omit origin metadata."""
    client, _deployment_id = _gateway(tmp_path, monkeypatch)
    path = "/__orcheo/workflows/lookup/runs"
    assert (
        client.post(
            path,
            headers={
                "host": "portal.apps.test",
                "content-type": "application/json",
                "idempotency-key": "request-1",
            },
            content="{}",
        ).status_code
        == 403
    )
    assert (
        client.post(
            path,
            headers={
                "host": "portal.apps.test",
                "content-type": "application/json",
                "origin": "https://portal.apps.test",
                "sec-fetch-site": "same-origin",
                "idempotency-key": "request-1",
                "authorization": "Bearer studio-token",
            },
            content="{}",
        ).status_code
        == 400
    )


def test_runtime_read_accepts_same_origin_fetch_without_origin_header(
    tmp_path: Path, monkeypatch
) -> None:
    """Safe same-origin GETs use Fetch Metadata when browsers omit Origin."""
    client, _deployment_id = _gateway(tmp_path, monkeypatch)
    path = "/__orcheo/runs/opaque-handle"

    response = client.get(
        path,
        headers={
            "host": "portal.apps.test",
            "sec-fetch-site": "same-origin",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "App runtime is unavailable."

    assert (
        client.get(
            path,
            headers={
                "host": "portal.apps.test",
                "origin": "https://attacker.example",
                "sec-fetch-site": "same-origin",
            },
        ).status_code
        == 403
    )
    assert client.get(path, headers={"host": "portal.apps.test"}).status_code == 403


def test_runtime_uses_signed_visitor_cookie_across_ip_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """Anonymous retries use an opaque gateway identity instead of source IP."""
    forwarded: list[dict[str, object]] = []

    class BackendClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **kwargs):
            forwarded.append(kwargs["json"])
            return httpx.Response(
                200,
                json={"handle": "opaque-handle", "status": "accepted"},
            )

    root = tmp_path / "bundles"
    root.mkdir()
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(root))
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_BACKEND_URL", "http://backend")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_SECRET", "g" * 64)
    monkeypatch.setattr(
        "orcheo_app_gateway.main.httpx.AsyncClient",
        lambda **_kwargs: BackendClient(),
    )
    app = create_app()
    headers = {
        "content-type": "application/json",
        "origin": "https://portal.apps.test",
        "sec-fetch-site": "same-origin",
        "idempotency-key": "request-1",
    }
    first_client = TestClient(
        app,
        base_url="https://portal.apps.test",
        client=("198.51.100.10", 443),
    )
    first = first_client.post(
        "/__orcheo/workflows/lookup/runs",
        headers=headers,
        content="{}",
    )

    assert first.status_code == 200
    cookie = first.headers["set-cookie"]
    assert "__Host-orcheo-app-visitor=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie

    second_client = TestClient(
        app,
        base_url="https://portal.apps.test",
        client=("203.0.113.8", 443),
        cookies=first_client.cookies,
    )
    second = second_client.post(
        "/__orcheo/workflows/lookup/runs",
        headers=headers,
        content="{}",
    )

    assert second.status_code == 200
    assert len(forwarded) == 2
    assert forwarded[0]["client_ip"] != forwarded[1]["client_ip"]
    assert forwarded[0]["anonymous_visitor_id"] == forwarded[1]["anonymous_visitor_id"]
    assert "set-cookie" not in second.headers


def test_gateway_starts_pkce_login_with_signed_httponly_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    """The browser receives only the central redirect and opaque auth cookie."""

    class BackendClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "deployment_id": str(uuid4()),
                    "visibility": "private",
                    "state": "published",
                },
            )

    root = tmp_path / "bundles"
    root.mkdir()
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(root))
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_BACKEND_URL", "http://backend")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_SECRET", "g" * 64)
    monkeypatch.setenv("ORCHEO_STUDIO_URL", "https://studio.test")
    monkeypatch.setattr(
        "orcheo_app_gateway.main.httpx.AsyncClient",
        lambda **_kwargs: BackendClient(),
    )
    client = TestClient(create_app())

    response = client.get(
        "/__orcheo/auth/start",
        params={"return_to": "/dashboard"},
        headers={"host": "portal.apps.test"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://studio.test/apps/authorize?"
    )
    cookie = response.headers["set-cookie"]
    assert "__Host-orcheo-app-auth=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_gateway_local_auth_callback_returns_to_requested_app_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Local callback URIs retain the gateway port and redirect after exchange."""

    class BackendClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "deployment_id": str(uuid4()),
                    "visibility": "private",
                    "state": "published",
                },
            )

        async def request(self, method, url, **_kwargs):
            assert method == "POST"
            assert url.endswith("/internal/hosted-apps/auth/exchange")
            return httpx.Response(200, json={"session_secret": "session-secret"})

    root = tmp_path / "bundles"
    root.mkdir()
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.localhost")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(root))
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_BACKEND_URL", "http://backend")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_SECRET", "g" * 64)
    monkeypatch.setenv("ORCHEO_STUDIO_URL", "http://localhost:2026")
    monkeypatch.setattr(
        "orcheo_app_gateway.main.httpx.AsyncClient",
        lambda **_kwargs: BackendClient(),
    )
    client = TestClient(create_app(), base_url="http://portal.apps.localhost:2030")

    started = client.get(
        "/__orcheo/auth/start",
        params={"return_to": "/dashboard"},
        headers={"host": "portal.apps.localhost:2030"},
        follow_redirects=False,
    )

    authorize_params = parse_qs(urlparse(started.headers["location"]).query)
    assert authorize_params["redirect_uri"] == [
        "http://portal.apps.localhost:2030/__orcheo/auth/callback"
    ]
    transaction = SimpleCookie(started.headers["set-cookie"])[
        "__Host-orcheo-app-auth"
    ].value
    callback = client.get(
        "/__orcheo/auth/callback",
        params={"code": "authorization-code", "state": authorize_params["state"][0]},
        headers={
            "host": "portal.apps.localhost:2030",
            "cookie": f"__Host-orcheo-app-auth={transaction}",
        },
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/dashboard"
    assert "__Host-orcheo-app-session=session-secret" in callback.headers["set-cookie"]
