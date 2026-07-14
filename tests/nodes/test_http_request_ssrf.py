"""SSRF-guard integration tests for ``HttpRequestNode``.

The guard is only active in restricted definition mode, where workflow authors
are untrusted. Trusted/unrestricted deployments keep unrestricted egress, so the
existing ``HttpRequestNode`` behaviour must be unchanged there.
"""

from __future__ import annotations
from datetime import timedelta
from typing import Any
import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.connectors.http_request import HttpRequestNode
from orcheo.security.ssrf import SSRFError, SSRFGuardAsyncTransport


def _capture_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``httpx.AsyncClient`` to capture init kwargs and stub requests."""
    captured: dict[str, Any] = {}
    original_init = httpx.AsyncClient.__init__

    def capture_init(self: httpx.AsyncClient, **kwargs: Any) -> None:
        captured["init_kwargs"] = kwargs
        original_init(self, **kwargs)

    async def fake_request(self: httpx.AsyncClient, **kwargs: Any) -> httpx.Response:
        captured["request_kwargs"] = kwargs
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
            extensions={"elapsed": timedelta(seconds=0.1)},
            request=httpx.Request(kwargs["method"], kwargs["url"]),
        )

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capture_init)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    return captured


@pytest.mark.asyncio
async def test_restricted_mode_blocks_internal_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In restricted mode a request to an internal address is rejected."""
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: True
    )
    captured = _capture_client(monkeypatch)

    node = HttpRequestNode(name="http", url="http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFError):
        await node(State({"node_results": {}}), RunnableConfig())

    # Pre-flight validation must reject before any client/request is created.
    assert "request_kwargs" not in captured


@pytest.mark.asyncio
async def test_restricted_mode_allows_public_url_with_guarded_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In restricted mode a public request runs through the guarded transport."""
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: True
    )
    captured = _capture_client(monkeypatch)

    node = HttpRequestNode(name="http", url="http://8.8.8.8/")
    result = await node(State({"node_results": {}}), RunnableConfig())

    assert result["node_results"]["http"]["json"] == {"ok": True}
    transport = captured["init_kwargs"].get("transport")
    assert isinstance(transport, SSRFGuardAsyncTransport)


@pytest.mark.asyncio
async def test_unrestricted_mode_does_not_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In unrestricted mode no guard is installed and internal URLs are allowed."""
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: False
    )
    captured = _capture_client(monkeypatch)

    node = HttpRequestNode(name="http", url="http://127.0.0.1:9000/internal")
    result = await node(State({"node_results": {}}), RunnableConfig())

    assert result["node_results"]["http"]["status_code"] == 200
    assert "transport" not in captured["init_kwargs"]
