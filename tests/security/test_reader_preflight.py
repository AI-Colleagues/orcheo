"""Test the installed-reader preflight's success and fail-closed outcomes."""

from __future__ import annotations
import asyncio
import runpy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call
import pytest
from orcheo.security import reader_preflight
from orcheo.security.ssrf import SSRFError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "response", "error", "blocked"),
    [
        ("http://example.org/", SimpleNamespace(status=403), None, True),
        ("http://example.org/", SimpleNamespace(status=200), None, False),
        ("http://example.org/", None, None, False),
        ("https://127.0.0.1/", SimpleNamespace(status=403), None, False),
        ("https://127.0.0.1/", None, RuntimeError("blocked"), True),
    ],
)
async def test_blocked_navigation_requires_refusal_and_closes_page(
    url: str, response: Any, error: Exception | None, blocked: bool
) -> None:
    """Accept refused HTTP or navigation errors and reject accessible targets."""
    page = Mock(
        goto=AsyncMock(return_value=response, side_effect=error), close=AsyncMock()
    )
    context = Mock(new_page=AsyncMock(return_value=page))
    if blocked:
        await reader_preflight._blocked_navigation(context, url)
    else:
        with pytest.raises(RuntimeError, match="allowed a forbidden navigation"):
            await reader_preflight._blocked_navigation(context, url)
    page.goto.assert_awaited_once_with(url, timeout=10000)
    page.close.assert_awaited_once()


@pytest.fixture
def reader_runtime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Provide an offline reader that blocks private navigation and HTTP calls."""
    monkeypatch.setenv("ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT", " ws://reader:3000 ")
    route = Mock(fulfill=AsyncMock())

    async def install_route(url: str, callback: Any) -> None:
        await callback(route)

    async def navigate(url: str, **kwargs: Any) -> Any:
        if url in (
            "https://example.org",
            "https://example.org/orcheo-security-fixture",
        ):
            return SimpleNamespace(ok=True)
        raise RuntimeError("navigation refused")

    page = Mock(
        goto=AsyncMock(side_effect=navigate),
        route=AsyncMock(side_effect=install_route),
        evaluate=AsyncMock(return_value="blocked"),
        close=AsyncMock(),
    )
    context = Mock(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = Mock(new_context=AsyncMock(return_value=context), close=AsyncMock())
    connect = AsyncMock(return_value=browser)
    playwright = Mock(
        __aenter__=AsyncMock(
            return_value=SimpleNamespace(chromium=SimpleNamespace(connect=connect))
        ),
        __aexit__=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        reader_preflight, "async_playwright", Mock(return_value=playwright)
    )
    client = Mock(get=AsyncMock(side_effect=SSRFError("forbidden target")))
    http = Mock(
        __aenter__=AsyncMock(return_value=client),
        __aexit__=AsyncMock(return_value=False),
    )
    factory = Mock(return_value=http)
    monkeypatch.setattr(reader_preflight.httpx, "AsyncClient", factory)
    # Transport construction is already covered by pinned-egress tests.
    monkeypatch.setattr(
        reader_preflight, "public_https_client_kwargs", lambda: {"trust_env": False}
    )
    return SimpleNamespace(
        page=page,
        route=route,
        context=context,
        browser=browser,
        connect=connect,
        client=client,
        factory=factory,
    )


@pytest.mark.asyncio
async def test_reader_preflight_checks_all_boundaries(
    reader_runtime: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report success only after browser, redirect, script, and client checks."""
    await reader_preflight.check_reader()
    reader_runtime.connect.assert_awaited_once_with("ws://reader:3000", timeout=30000)
    reader_runtime.browser.new_context.assert_awaited_once_with(service_workers="block")
    assert reader_runtime.page.goto.await_args_list == [
        call("https://example.org", timeout=30000),
        call("https://127.0.0.1/", timeout=10000),
        call("https://169.254.169.254/", timeout=10000),
        call("https://10.0.0.1/", timeout=10000),
        call("https://[::1]/", timeout=10000),
        call("http://example.org/", timeout=10000),
        call("https://example.org/orcheo-security-redirect", timeout=10000),
        call("https://example.org/orcheo-security-fixture"),
    ]
    assert reader_runtime.route.fulfill.await_args_list == [
        call(status=302, headers={"location": "https://127.0.0.1/"}),
        call(content_type="text/html", body="<p>Security fixture</p>"),
    ]
    assert "169.254.169.254" in reader_runtime.page.evaluate.call_args.args[0]
    reader_runtime.context.close.assert_awaited_once()
    reader_runtime.browser.close.assert_awaited_once()
    reader_runtime.factory.assert_called_once_with(trust_env=False)
    assert reader_runtime.client.get.await_args_list == [
        call("https://127.0.0.1/"),
        call("https://169.254.169.254/"),
        call("http://example.org/"),
    ]
    assert "Public reader preflight passed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_reader_preflight_requires_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject absent or blank remote configuration before launching Playwright."""
    monkeypatch.setenv("ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT", " ")
    with pytest.raises(
        RuntimeError, match="configure ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT"
    ):
        await reader_preflight.check_reader()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, SimpleNamespace(ok=False)])
async def test_reader_preflight_rejects_failed_public_https(
    reader_runtime: SimpleNamespace, response: Any
) -> None:
    """Close the browser and fail when public HTTPS cannot be validated."""
    reader_runtime.page.goto.side_effect = None
    reader_runtime.page.goto.return_value = response
    with pytest.raises(RuntimeError, match="public HTTPS navigation failed"):
        await reader_preflight.check_reader()
    reader_runtime.browser.close.assert_awaited_once()
    reader_runtime.client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_preflight_rejects_script_access_to_private_target(
    reader_runtime: SimpleNamespace,
) -> None:
    """Treat page-script access to metadata as a failed deployment check."""
    reader_runtime.page.evaluate.return_value = "reachable"
    with pytest.raises(RuntimeError, match="page script reached a forbidden target"):
        await reader_preflight.check_reader()
    reader_runtime.browser.close.assert_awaited_once()
    reader_runtime.client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_reader_preflight_rejects_unguarded_http_client(
    reader_runtime: SimpleNamespace,
) -> None:
    """Fail if the worker HTTP client accepts even one forbidden target."""
    reader_runtime.client.get.side_effect = None
    with pytest.raises(
        RuntimeError, match="public HTTP client allowed a forbidden target"
    ):
        await reader_preflight.check_reader()
    reader_runtime.client.get.assert_awaited_once_with("https://127.0.0.1/")


def test_reader_preflight_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the asynchronous installed-image check when invoked as a script."""
    executed = []

    def run(coroutine: Any) -> None:
        executed.append(coroutine.cr_code.co_name)
        coroutine.close()

    monkeypatch.setattr(asyncio, "run", run)
    runpy.run_path(reader_preflight.__file__, run_name="__main__")
    assert executed == ["check_reader"]
