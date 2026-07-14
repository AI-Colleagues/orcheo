"""SSRF-guard coverage for author-URL httpx egress nodes beyond HttpRequestNode.

``RSSNode`` and ``WebDocumentLoaderNode`` both fetch author-controlled URLs with
``httpx.AsyncClient``. They must install the SSRF-guarded transport in restricted
mode (so the guard cannot be bypassed by choosing a different node) and leave
egress unrestricted otherwise.
"""

from __future__ import annotations
from typing import Any
import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.rag.ingestion import WebDocumentLoaderNode
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

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capture_init)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
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
