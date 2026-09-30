"""Connection-time egress checks for untrusted browser sessions."""

from __future__ import annotations
import asyncio
import socket
from typing import Any
import pytest
from orcheo.nodes.browser_proxy import (
    PublicHttpsProxy,
    _connect_host,
    _public_addresses,
)


@pytest.mark.parametrize(
    "authority",
    [
        "127.0.0.1:443",
        "localhost:443",
        "example.com:80",
        "user@example.com:443",
        "example.com:443/path",
    ],
)
@pytest.mark.asyncio
async def test_proxy_refuses_private_and_non_https_targets(authority: str) -> None:
    """A browser cannot use the proxy to reach local or plain HTTP services."""
    proxy = PublicHttpsProxy()
    await proxy.start()
    try:
        host, port = proxy.url.removeprefix("http://").split(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        writer.write(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        assert (await reader.read(128)).startswith(b"HTTP/1.1 403")
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


def test_connect_authority_requires_port_443() -> None:
    """Only a full HTTPS CONNECT authority is accepted."""
    assert _connect_host("example.com:443") == "example.com"
    with pytest.raises(ValueError, match="port 443"):
        _connect_host("example.com:8443")


@pytest.mark.asyncio
async def test_dns_answer_must_be_entirely_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed public/private answer is rejected before any socket connects."""
    loop = asyncio.get_running_loop()

    async def mixed(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", mixed)
    with pytest.raises(ValueError, match="non-public"):
        await _public_addresses("example.com")


@pytest.mark.asyncio
async def test_proxy_connects_to_the_checked_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upstream socket uses the vetted IP rather than resolving again."""
    destinations: list[tuple[str, int]] = []

    async def upstream(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(await reader.read(4))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    proxy = PublicHttpsProxy()
    await proxy.start()
    try:
        proxy_host, proxy_port = proxy.url.removeprefix("http://").split(":")
        reader, writer = await asyncio.open_connection(proxy_host, int(proxy_port))

        async def public_addresses(host: str) -> list[tuple[Any, ...]]:
            assert host == "example.com"
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]

        real_open = asyncio.open_connection

        async def pinned_open(
            host: str, port: int, **kwargs: Any
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            del kwargs
            destinations.append((host, port))
            return await real_open("127.0.0.1", upstream_port)

        monkeypatch.setattr(
            "orcheo.nodes.browser_proxy._public_addresses", public_addresses
        )
        monkeypatch.setattr(asyncio, "open_connection", pinned_open)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"ping")
        await writer.drain()
        assert await reader.readexactly(4) == b"ping"
        assert destinations == [("8.8.8.8", 443)]
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()
