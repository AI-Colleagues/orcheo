"""Check public-reader behavior against the installed browser and worker image."""

from __future__ import annotations
import asyncio
import os
from typing import Any
import httpx
from playwright.async_api import async_playwright
from orcheo.security.ssrf import SSRFError, public_https_client_kwargs


async def _blocked_navigation(
    context: Any, url: str, *, redirect: bool = False
) -> None:
    """Require the installed browser to refuse a private or non-HTTPS target."""
    page = await context.new_page()
    try:
        if redirect:
            destination = url
            await page.route(
                "https://example.org/orcheo-security-redirect",
                lambda route: route.fulfill(
                    status=302, headers={"location": destination}
                ),
            )
            url = "https://example.org/orcheo-security-redirect"
        try:
            response = await page.goto(url, timeout=10000)
            # Plain HTTP is refused by the CONNECT-only proxy with an HTTP
            # 403 response; Chromium displays that response without raising.
            if url.startswith("http://") and response and response.status == 403:
                return
        except Exception:
            return
        raise RuntimeError("public browser allowed a forbidden navigation")
    finally:
        await page.close()


async def check_reader() -> None:
    """Verify public HTTPS, blocked private targets, redirects, and the client."""
    endpoint = os.getenv("ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("configure ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT first")
    async with async_playwright() as runtime:
        browser = await runtime.chromium.connect(endpoint, timeout=30000)
        try:
            context = await browser.new_context(service_workers="block")
            page = await context.new_page()
            response = await page.goto("https://example.org", timeout=30000)
            if response is None or not response.ok:
                raise RuntimeError("public HTTPS navigation failed")
            await page.close()
            for url in (
                "https://127.0.0.1/",
                "https://169.254.169.254/",
                "https://10.0.0.1/",
                "https://[::1]/",
                "http://example.org/",
            ):
                await _blocked_navigation(context, url)
            await _blocked_navigation(context, "https://127.0.0.1/", redirect=True)
            page = await context.new_page()
            await page.route(
                "https://example.org/orcheo-security-fixture",
                lambda route: route.fulfill(
                    content_type="text/html", body="<p>Security fixture</p>"
                ),
            )
            await page.goto("https://example.org/orcheo-security-fixture")
            result = await page.evaluate("""async () => {
                try { await fetch('https://169.254.169.254/'); return 'reachable'; }
                catch { return 'blocked'; }
            }""")
            if result != "blocked":
                raise RuntimeError("page script reached a forbidden target")
            await context.close()
        finally:
            await browser.close()
    async with httpx.AsyncClient(**public_https_client_kwargs()) as client:
        for url in (
            "https://127.0.0.1/",
            "https://169.254.169.254/",
            "http://example.org/",
        ):
            try:
                await client.get(url)
            except SSRFError:
                continue
            raise RuntimeError("public HTTP client allowed a forbidden target")
    print(
        "Public reader preflight passed: HTTPS, private IPs, redirects, "
        "page scripts, HTTP client."
    )


if __name__ == "__main__":
    asyncio.run(check_reader())
