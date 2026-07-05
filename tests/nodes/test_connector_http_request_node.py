"""Coverage tests for legacy connector HTTP request alias."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from httpx import Response
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.connectors.http_request import HttpRequestNode


@pytest.mark.asyncio
async def test_connector_http_request_node_returns_response_metadata() -> None:
    state = State({"node_results": {}})
    node = HttpRequestNode(
        name="http",
        method="GET",
        url="https://example.com/api",
    )

    with respx.mock(base_url="https://example.com") as router:
        router.get("/api").respond(200, json={"status": "ok"})
        payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert payload["status_code"] == 200
    assert payload["json"] == {"status": "ok"}
    assert payload["url"].startswith("https://example.com/api")


@pytest.mark.asyncio
async def test_connector_http_request_node_raises_for_http_errors() -> None:
    state = State({"node_results": {}})
    node = HttpRequestNode(
        name="http",
        method="GET",
        url="https://example.com/not-found",
        raise_for_status=True,
    )

    with respx.mock(base_url="https://example.com") as router:
        router.get("/not-found").mock(
            return_value=Response(404, json={"error": "nope"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_connector_http_request_node_handles_non_json_response() -> None:
    state = State({"node_results": {}})
    node = HttpRequestNode(
        name="http",
        method="POST",
        url="https://example.com/api",
        content="payload",
    )

    with respx.mock(base_url="https://example.com") as router:
        router.post("/api").mock(
            return_value=Response(
                200,
                text="ok",
                extensions={"elapsed": timedelta(seconds=0.5)},
            )
        )
        payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert payload["json"] is None
    assert payload["elapsed"] is not None and payload["elapsed"] >= 0
    assert payload["content"] == "ok"


@pytest.mark.asyncio
async def test_connector_http_request_node_sends_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(
            201,
            json={"ok": True},
            extensions={"elapsed": timedelta(seconds=1)},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    node = HttpRequestNode(
        name="http",
        method="PUT",
        url="https://example.com/api",
        json_body={"alpha": 1},
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert captured == {
        "method": "PUT",
        "url": "https://example.com/api",
        "json": {"alpha": 1},
    }
    assert payload["json"] == {"ok": True}
    assert payload["elapsed"] == 1.0


@pytest.mark.asyncio
async def test_connector_http_request_node_sends_form_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        return httpx.Response(
            200,
            json={"success": True},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    node = HttpRequestNode(
        name="http",
        method="POST",
        url="https://example.com/form",
        data={"field1": "value1", "field2": "value2"},
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert captured["data"] == {"field1": "value1", "field2": "value2"}
    assert payload["json"] == {"success": True}


@pytest.mark.asyncio
async def test_connector_http_request_node_uses_extension_elapsed_when_property_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MockResponse(httpx.Response):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)

        @property
        def elapsed(self) -> timedelta:
            raise RuntimeError("elapsed not ready")

    async def fake_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        del self, method, url, kwargs
        return MockResponse(
            200,
            json={"ok": True},
            extensions={"elapsed": timedelta(seconds=3.5)},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    node = HttpRequestNode(
        name="http",
        method="GET",
        url="https://example.com/api",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert payload["elapsed"] == 3.5


@pytest.mark.asyncio
async def test_connector_http_request_node_uses_original_url_when_response_url_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MockResponse(httpx.Response):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)

        @property
        def url(self) -> httpx.URL:
            raise RuntimeError("url not ready")

    async def fake_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        del self, method, url, kwargs
        return MockResponse(200, text="ok")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    node = HttpRequestNode(
        name="http",
        method="GET",
        url="https://example.com/fallback",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["http"]

    assert payload["url"] == "https://example.com/fallback"


@pytest.mark.asyncio
async def test_connector_http_request_node_wraps_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        del self, method, url, kwargs
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    node = HttpRequestNode(
        name="http",
        method="GET",
        url="https://example.com/error",
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError, match="HTTP request failed: boom"):
        await node(state, RunnableConfig())
