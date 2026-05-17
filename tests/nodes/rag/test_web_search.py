"""Tests for WebSearchNode Tavily integration."""

from __future__ import annotations
import json
from typing import Any
import httpx
import pytest
import respx
from langchain_core.runnables import RunnableConfig
import orcheo.nodes.rag.retrieval as retrieval_mod
from orcheo.graph.state import State
from orcheo.nodes.rag import WebSearchNode
from orcheo.nodes.rag.models import SearchResult
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from tests.runtime.credential_test_helpers import create_vault_with_secret


@pytest.mark.asyncio
async def test_web_search_node_requests_tavily_and_formats_results() -> None:
    """WebSearchNode should call Tavily and normalize results."""
    state = State(
        inputs={"query": "latest ai news"},
        results={},
        structured_response=None,
    )
    node = WebSearchNode(
        name="web",
        api_key="tavily-key",
        max_results=3,
        include_raw_content=True,
        search_depth="advanced",
        days=2,
        topic="news",
        include_domains=["example.com"],
        exclude_domains=["ignore.com"],
    )

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "answer": "summary answer",
                "results": [
                    {
                        "title": "Example Title",
                        "url": "https://example.com",
                        "content": "snippet",
                        "score": 0.9,
                        "raw_content": "full page text",
                    },
                    {"url": "https://example.com/2", "score": "not-a-number"},
                    {"content": "", "raw_content": "raw-only"},
                ],
            },
        )

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(side_effect=handler)
        result = await node(state, RunnableConfig())

    payload = result["results"]["web"]
    request_json = captured["json"]

    assert request_json["api_key"] == "tavily-key"
    assert request_json["query"] == "latest ai news"
    assert request_json["max_results"] == 3
    assert request_json["include_raw_content"] is True
    assert request_json["search_depth"] == "advanced"
    assert request_json["days"] == 2
    assert request_json["topic"] == "news"
    assert request_json["include_domains"] == ["example.com"]
    assert request_json["exclude_domains"] == ["ignore.com"]

    assert payload["answer"] == "summary answer"
    assert payload["source"] == "web"
    raw_results = payload["results"]
    results = [SearchResult.model_validate(entry) for entry in raw_results]
    assert results[0].id == "https://example.com"
    assert results[0].metadata["url"] == "https://example.com"
    assert results[0].metadata["title"] == "Example Title"
    assert results[0].metadata["raw_content"] == "full page text"
    assert results[0].score == pytest.approx(0.9)
    assert results[0].source == "web"
    assert results[1].id == "https://example.com/2"
    assert results[1].score == 0.0
    assert results[1].text == "https://example.com/2"
    assert results[2].id == "web-2"
    assert results[2].metadata == {"raw_content": "raw-only"}
    assert results[2].text == "raw-only"


@pytest.mark.asyncio
async def test_web_search_node_rejects_unknown_provider() -> None:
    node = WebSearchNode(
        name="web", api_key="key", provider="bing", suppress_errors=False
    )
    state = State(
        inputs={"query": "news"},
        results={},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="WebSearchNode only supports the 'tavily' provider"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_requires_query_when_not_suppressed() -> None:
    """WebSearchNode should reject empty queries when suppression is disabled."""
    node = WebSearchNode(name="web", api_key="key", suppress_errors=False)
    state = State(
        inputs={"query": "   "},
        results={},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="WebSearchNode requires a non-empty query string"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_requires_api_key_when_not_suppressed() -> None:
    """WebSearchNode should require api_key when suppression disabled."""
    node = WebSearchNode(name="web", suppress_errors=False)
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="WebSearchNode requires api_key to be configured"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_rejects_whitespace_api_key_when_not_suppressed() -> None:
    """WebSearchNode should treat whitespace api_key as missing."""
    node = WebSearchNode(name="web", api_key="   ", suppress_errors=False)
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    with pytest.raises(
        ValueError, match="WebSearchNode requires api_key to be configured"
    ):
        await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_resolves_api_key_from_credential_vault() -> None:
    """WebSearchNode should resolve api_key placeholders via active resolver."""
    state = State(
        inputs={"query": "latest ai news"},
        results={},
        structured_response=None,
    )
    node = WebSearchNode(
        name="web", api_key="[[telegram_token]]", suppress_errors=False
    )
    resolver = CredentialResolver(create_vault_with_secret(secret="vault-key"))

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"results": []})

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(side_effect=handler)
        with credential_resolution(resolver):
            await node.run(state, {})

    assert captured["json"]["api_key"] == "vault-key"


def test_web_search_node_raises_for_placeholder_without_active_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential placeholders require an active credential resolver."""
    node = WebSearchNode(name="web", api_key="[[telegram_token]]")
    monkeypatch.setattr(retrieval_mod, "get_active_credential_resolver", lambda: None)

    with pytest.raises(ValueError, match="no active credential resolver is available"):
        node._resolve_api_key()


def test_web_search_node_raises_when_resolved_credential_is_not_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved credential values must be strings."""

    class _BadResolver:
        def resolve(self, reference: object) -> int:
            del reference
            return 123

    node = WebSearchNode(name="web", api_key="[[telegram_token]]")
    monkeypatch.setattr(
        retrieval_mod, "get_active_credential_resolver", lambda: _BadResolver()
    )

    with pytest.raises(
        ValueError, match="Resolved WebSearchNode api_key credential must be a string"
    ):
        node._resolve_api_key()


@pytest.mark.asyncio
async def test_web_search_node_handles_http_errors_when_not_suppressed() -> None:
    """WebSearchNode should surface HTTP failures when suppression disabled."""
    node = WebSearchNode(name="web", api_key="key", suppress_errors=False)
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(500, text="error")
        )
        with pytest.raises(ValueError, match="Web search request failed"):
            await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_validates_response_shape_when_not_suppressed() -> None:
    """WebSearchNode should validate Tavily payloads when suppression disabled."""
    node = WebSearchNode(name="web", api_key="key", suppress_errors=False)
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(200, json={"results": "not-a-list"})
        )
        with pytest.raises(
            ValueError, match="WebSearchNode expected 'results' list in response"
        ):
            await node.run(state, {})

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(200, json={"results": ["bad-entry"]})
        )
        with pytest.raises(
            ValueError, match="Each web search result must be a mapping"
        ):
            await node.run(state, {})

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(200, text="not-json")
        )
        with pytest.raises(
            ValueError, match="WebSearchNode received non-JSON response"
        ):
            await node.run(state, {})


@pytest.mark.asyncio
async def test_web_search_node_returns_warning_when_query_missing() -> None:
    """WebSearchNode should return warning payload when query is missing by default."""
    node = WebSearchNode(name="web", api_key="key")
    state = State(
        inputs={"query": "   "},
        results={},
        structured_response=None,
    )

    payload = await node.run(state, {})
    assert payload["results"] == []
    assert "warning" in payload
    assert payload["source"] == "web"


@pytest.mark.asyncio
async def test_web_search_node_suppresses_http_errors_by_default() -> None:
    """WebSearchNode should suppress HTTP errors when suppress_errors is True."""
    node = WebSearchNode(name="web", api_key="key")
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(500, text="error")
        )
        payload = await node.run(state, {})

    assert payload["results"] == []
    assert "warning" in payload
    assert payload["source"] == "web"


@pytest.mark.asyncio
async def test_web_search_node_suppresses_unexpected_errors_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebSearchNode should suppress unexpected exceptions when configured."""
    node = WebSearchNode(name="web", api_key="key")
    state = State(
        inputs={"query": "hello"},
        results={},
        structured_response=None,
    )

    async def boom(_: State) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(node, "_run_search", boom)
    payload = await node.run(state, {})
    assert payload["results"] == []
    assert payload["warning"] == "web search unavailable: kaboom"
    assert payload["source"] == "web"
