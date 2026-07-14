"""SSRF-guard coverage for author-URL httpx egress nodes beyond HttpRequestNode.

HTTP, RSS, web-loader, web-search, Discord webhook, and SMTP nodes accept
author-controlled network targets. They must reject internal targets in
restricted mode and leave egress unrestricted otherwise.
"""

from __future__ import annotations
from typing import Any
import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.communication import DiscordWebhookNode, EmailNode
from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode
from orcheo.nodes.rag.retrieval import WebSearchNode
from orcheo.nodes.rss import RSSNode
from orcheo.security.ssrf import SSRFError, SSRFGuardAsyncTransport


def _capture_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``httpx.AsyncClient`` to capture whether a guarded transport is set."""
    captured: dict[str, Any] = {}
    original_init = httpx.AsyncClient.__init__

    def capture_init(self: httpx.AsyncClient, **kwargs: Any) -> None:
        captured["transport"] = kwargs.get("transport")
        original_init(self, **kwargs)

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        # Enforce the guard the real transport would apply, without networking.
        transport = captured.get("transport")
        if isinstance(transport, SSRFGuardAsyncTransport):
            await transport.handle_async_request(httpx.Request("GET", url))
        return httpx.Response(
            200,
            text="<html><body>hello world</body></html>",
            request=httpx.Request("GET", url),
        )

    async def fake_post(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        transport = captured.get("transport")
        if isinstance(transport, SSRFGuardAsyncTransport):
            await transport.handle_async_request(httpx.Request("POST", url))
        return httpx.Response(
            200,
            json={"results": []},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capture_init)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def _set_restricted(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: value
    )


@pytest.mark.asyncio
async def test_rss_node_guards_egress_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RSSNode installs the guarded transport and blocks an internal feed URL."""
    _set_restricted(monkeypatch, True)
    _capture_transport(monkeypatch)

    node = RSSNode(name="rss", sources=["http://169.254.169.254/feed.xml"])
    with pytest.raises(SSRFError):
        await node(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_rss_node_unrestricted_mode_is_not_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RSSNode installs no guard outside restricted mode."""
    _set_restricted(monkeypatch, False)
    captured = _capture_transport(monkeypatch)

    node = RSSNode(name="rss", sources=["http://127.0.0.1/feed.xml"])
    await node(State({"node_results": {}}), RunnableConfig())

    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_web_document_loader_guards_egress_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebDocumentLoaderNode blocks an internal URL in restricted mode."""
    _set_restricted(monkeypatch, True)
    _capture_transport(monkeypatch)

    node = WebDocumentLoaderNode(
        name="web", urls=[{"url": "http://127.0.0.1:8500/v1/secret"}]
    )
    with pytest.raises(SSRFError):
        await node(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_web_document_loader_unrestricted_mode_is_not_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebDocumentLoaderNode installs no guard outside restricted mode."""
    _set_restricted(monkeypatch, False)
    captured = _capture_transport(monkeypatch)

    node = WebDocumentLoaderNode(name="web", urls=[{"url": "http://10.0.0.1/page"}])
    await node(State({"node_results": {}}), RunnableConfig())

    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_web_search_guards_configurable_endpoint_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebSearchNode blocks an internal author-configured API endpoint."""
    _set_restricted(monkeypatch, True)
    _capture_transport(monkeypatch)

    node = WebSearchNode(
        name="web",
        api_key="key",
        api_url="http://127.0.0.1/search",
        suppress_errors=False,
    )
    with pytest.raises(SSRFError):
        await node(State(inputs={"query": "news"}), RunnableConfig())


@pytest.mark.asyncio
async def test_web_search_unrestricted_mode_is_not_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WebSearchNode installs no guard outside restricted mode."""
    _set_restricted(monkeypatch, False)
    captured = _capture_transport(monkeypatch)

    node = WebSearchNode(
        name="web",
        api_key="key",
        api_url="http://10.0.0.1/search",
        suppress_errors=False,
    )
    await node(State(inputs={"query": "news"}), RunnableConfig())

    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_discord_webhook_guards_egress_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DiscordWebhookNode blocks an internal webhook URL."""
    _set_restricted(monkeypatch, True)
    _capture_transport(monkeypatch)

    node = DiscordWebhookNode(
        name="discord",
        webhook_url="http://127.0.0.1/api/webhooks/123",
        content="hello",
    )
    with pytest.raises(SSRFError):
        await node(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_discord_webhook_unrestricted_mode_is_not_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DiscordWebhookNode installs no guard outside restricted mode."""
    _set_restricted(monkeypatch, False)
    captured = _capture_transport(monkeypatch)

    node = DiscordWebhookNode(
        name="discord",
        webhook_url="http://10.0.0.1/api/webhooks/123",
        content="hello",
    )
    await node(State({"node_results": {}}), RunnableConfig())

    assert captured["transport"] is None


@pytest.mark.asyncio
async def test_email_blocks_internal_smtp_host_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EmailNode rejects SMTP targets on the Compose network."""
    _set_restricted(monkeypatch, True)

    node = EmailNode(
        name="email",
        smtp_host="127.0.0.1",
        from_address="sender@example.com",
        to_addresses=["recipient@example.com"],
    )
    with pytest.raises(SSRFError):
        await node(State({"node_results": {}}), RunnableConfig())


@pytest.mark.asyncio
async def test_email_unrestricted_mode_skips_host_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EmailNode keeps unrestricted SMTP behavior outside restricted mode."""
    _set_restricted(monkeypatch, False)
    monkeypatch.setattr(
        EmailNode,
        "_send_email",
        lambda self: {"accepted": list(self.to_addresses), "refused": {}},
    )

    node = EmailNode(
        name="email",
        smtp_host="127.0.0.1",
        from_address="sender@example.com",
        to_addresses=["recipient@example.com"],
    )
    result = await node(State({"node_results": {}}), RunnableConfig())

    assert result["node_results"]["email"]["accepted"] == ["recipient@example.com"]
