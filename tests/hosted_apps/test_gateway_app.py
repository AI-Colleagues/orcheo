"""ASGI delivery tests for manifest-only Hosted Apps serving."""

from __future__ import annotations

import json
from pathlib import Path
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
