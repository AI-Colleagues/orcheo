"""Tests for ChatKit asset proxy routes."""

import httpx
import respx
from fastapi.testclient import TestClient


def test_proxy_chatkit_deployment_asset(api_client: TestClient) -> None:
    """ChatKit deployment assets are proxied through the API route."""
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://cdn.platform.openai.com/deployments/chatkit/chatkit.js"
        ).mock(
            return_value=httpx.Response(
                200,
                content=b"console.log('chatkit');",
                headers={"Content-Type": "application/javascript"},
            )
        )
        response = api_client.get("/api/chatkit/assets/chatkit.js")
        assert response.status_code == 200
        assert response.content == b"console.log('chatkit');"
        assert response.headers["content-type"].startswith("application/javascript")


def test_proxy_chatkit_deployment_html_rewrites_ck1_paths(
    api_client: TestClient,
) -> None:
    """ChatKit deployment HTML rewrites CK1 asset paths to API routes."""
    html = (
        "<head>"
        '<script src="/assets/ck1/index.js"></script>'
        '<link rel="stylesheet" href="/assets/ck1/index.css">'
        "</head>"
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://cdn.platform.openai.com/deployments/chatkit/index-test.html"
        ).mock(
            return_value=httpx.Response(
                200,
                text=html,
                headers={"Content-Type": "text/html"},
            )
        )
        response = api_client.get("/api/chatkit/assets/index-test.html")
        assert response.status_code == 200
        assert "/api/chatkit/assets/ck1/index.js" in response.text
        assert "/api/chatkit/assets/ck1/index.css" in response.text
        assert "data-orcheo-fetch-guard" in response.text


def test_proxy_ck1_asset(api_client: TestClient) -> None:
    """CK1 assets are proxied from the CDN root path."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://cdn.platform.openai.com/assets/ck1/index.js").mock(
            return_value=httpx.Response(200, content=b"console.log('ck1');")
        )
        response = api_client.get("/assets/ck1/index.js")
        assert response.status_code == 200
        assert response.content == b"console.log('ck1');"


def test_proxy_ck1_asset_under_api_prefix(api_client: TestClient) -> None:
    """CK1 assets are proxied under the API namespace for hosted deployments."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://cdn.platform.openai.com/assets/ck1/index.js").mock(
            return_value=httpx.Response(200, content=b"console.log('ck1');")
        )
        response = api_client.get("/api/chatkit/assets/ck1/index.js")
        assert response.status_code == 200
        assert response.content == b"console.log('ck1');"


def test_proxy_ck1_analytics_bundle_is_stubbed(api_client: TestClient) -> None:
    """Analytics bundles are replaced to avoid client-side fetch failures."""
    js_payload = b"const AnalyticsBrowser = {}; const name = 'Segment.io';"
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://cdn.platform.openai.com/assets/ck1/index-analytics.js"
        ).mock(
            return_value=httpx.Response(
                200,
                content=js_payload,
                headers={"Content-Type": "application/javascript"},
            )
        )
        response = api_client.get("/api/chatkit/assets/ck1/index-analytics.js")
        assert response.status_code == 200
        assert "orcheo-analytics-stub" in response.text


def test_proxy_ck1_large_bundle_not_stubbed(api_client: TestClient) -> None:
    """Large bundles containing analytics strings are NOT stubbed."""
    # Create a payload > 500KB that contains analytics strings
    padding = b"x" * 600_000
    js_payload = b"const AnalyticsBrowser = {}; const name = 'Segment.io';" + padding
    with respx.mock(assert_all_called=True) as router:
        router.get("https://cdn.platform.openai.com/assets/ck1/index-main.js").mock(
            return_value=httpx.Response(
                200,
                content=js_payload,
                headers={"Content-Type": "application/javascript"},
            )
        )
        response = api_client.get("/api/chatkit/assets/ck1/index-main.js")
        assert response.status_code == 200
        # Should return original content, not the stub
        assert "orcheo-analytics-stub" not in response.text
        assert b"AnalyticsBrowser" in response.content


def test_proxy_chatkit_deployment_html_strips_cloudflare_challenge(
    api_client: TestClient,
) -> None:
    """Cloudflare challenge scripts are stripped from proxied HTML."""
    html = (
        "<head>"
        '<script src="/assets/ck1/index.js"></script>'
        "</head>"
        "<body>"
        "<div id='root'></div>"
        "<script>(function(){var a=document.createElement('script');"
        "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';"
        "document.getElementsByTagName('head')[0].appendChild(a);})()</script>"
        "</body>"
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://cdn.platform.openai.com/deployments/chatkit/index-cf.html"
        ).mock(
            return_value=httpx.Response(
                200,
                text=html,
                headers={"Content-Type": "text/html"},
            )
        )
        response = api_client.get("/api/chatkit/assets/index-cf.html")
        assert response.status_code == 200
        # CK1 paths should be rewritten
        assert "/api/chatkit/assets/ck1/index.js" in response.text
        # Cloudflare challenge script should be stripped
        assert "/cdn-cgi/challenge-platform/" not in response.text
        # Other content should remain
        assert "<div id='root'></div>" in response.text


def test_proxy_chatkit_asset_forwards_query_string(api_client: TestClient) -> None:
    """Query string parameters are forwarded to the upstream CDN."""
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://cdn.platform.openai.com/assets/ck1/index.js?v=123&foo=bar"
        ).mock(
            return_value=httpx.Response(
                200,
                content=b"console.log('versioned');",
                headers={"Content-Type": "application/javascript"},
            )
        )
        response = api_client.get("/api/chatkit/assets/ck1/index.js?v=123&foo=bar")
        assert response.status_code == 200
        assert response.content == b"console.log('versioned');"


def test_proxy_chatkit_asset_rejects_post_method(api_client: TestClient) -> None:
    """POST requests to asset proxy return 405 Method Not Allowed."""
    response = api_client.post("/api/chatkit/assets/ck1/index.js")
    assert response.status_code == 405


def test_proxy_chatkit_asset_request_error_returns_502(
    api_client: TestClient,
) -> None:
    """Network errors when fetching upstream return 502 Bad Gateway."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://cdn.platform.openai.com/assets/ck1/index.js").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        response = api_client.get("/api/chatkit/assets/ck1/index.js")
        assert response.status_code == 502
        assert "Failed to fetch ChatKit asset" in response.json()["detail"]
