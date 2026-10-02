"""Verify isolated reader startup and executable module entry points."""

from __future__ import annotations
import asyncio
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
import pytest
from playwright._impl import _driver
from orcheo.security import https_proxy as browser_proxy
from orcheo.security import browser_worker, egress_worker, https_proxy_worker


def test_browser_worker_launches_fixed_isolated_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass the Playwright package and fixed egress settings to its Node runtime."""
    monkeypatch.setattr(
        _driver,
        "compute_driver_executable",
        lambda: ("/runtime/node", "/runtime/cli.js"),
    )
    execute = Mock()
    monkeypatch.setattr(browser_worker.os, "execv", execute)
    runpy.run_path(str(Path(browser_worker.__file__)), run_name="__main__")
    execute.assert_called_once()
    node, args = execute.call_args.args
    assert node == "/runtime/node"
    assert args[:2] == [node, "-e"]
    script = args[2]
    assert 'require("/runtime")' in script
    assert "chromium.launchServer" in script
    assert "wsPath: '/public-browser', headless: true" in script
    assert "server: 'http://public-egress:8080', bypass: ''" in script
    assert "--proxy-bypass-list=<-loopback>" in script
    assert "--disable-quic" in script
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in script
    assert "await server.close(); process.exit(0)" in script


def test_egress_worker_entry_point_applies_firewall_before_dropping_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise module startup without modifying the host's firewall or process."""
    applied = Mock()
    execute = Mock()
    monkeypatch.setattr(egress_worker.subprocess, "run", applied)
    monkeypatch.setattr(egress_worker.os, "execvp", execute)
    runpy.run_path(egress_worker.__file__, run_name="__main__")
    assert applied.call_args_list == [
        ((command,), {"check": True}) for command in egress_worker.firewall_commands()
    ]
    execute.assert_called_once_with(
        "gosu", ["gosu", "orcheo", "python", "-m", "orcheo.security.https_proxy_worker"]
    )


@pytest.mark.asyncio
async def test_proxy_sidecar_closes_listener_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind the internal sidecar port and release it on shutdown."""
    proxy = Mock(start=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(browser_proxy, "PublicHttpsProxy", Mock(return_value=proxy))
    monkeypatch.setattr(
        browser_proxy.asyncio,
        "Event",
        Mock(return_value=Mock(wait=AsyncMock(side_effect=asyncio.CancelledError))),
    )
    with pytest.raises(asyncio.CancelledError):
        await browser_proxy._serve()
    proxy.start.assert_awaited_once_with(host="0.0.0.0", port=8080)
    proxy.close.assert_awaited_once()


@pytest.mark.parametrize("module", [browser_proxy, https_proxy_worker])
def test_proxy_module_entry_points_start_server(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    """Delegate both executable proxy modules to the asynchronous server."""
    executed = []

    def run(coroutine: Any) -> None:
        executed.append(coroutine.cr_code.co_name)
        coroutine.close()

    monkeypatch.setattr(asyncio, "run", run)
    runpy.run_path(module.__file__, run_name="__main__")
    assert executed == ["_serve"]
