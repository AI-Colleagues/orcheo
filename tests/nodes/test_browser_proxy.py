"""Connection-time egress checks for untrusted browser sessions."""

from __future__ import annotations
import asyncio
import socket
from typing import Any
from unittest.mock import AsyncMock, Mock
import pytest
from orcheo.security.https_proxy import (
    PublicHttpsProxy,
    _connect_host,
    _pipe,
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
    with pytest.raises(ValueError, match="invalid CONNECT port"):
        _connect_host("example.com:not-a-port")


def test_proxy_url_requires_a_running_listener() -> None:
    """Callers cannot configure a browser with a proxy that has not started."""
    with pytest.raises(RuntimeError, match="not running"):
        _ = PublicHttpsProxy().url


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
async def test_dns_empty_and_mapped_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject missing DNS and mapped private IPv4; accept a public answer."""
    loop = asyncio.get_running_loop()

    async def empty(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return []

    monkeypatch.setattr(loop, "getaddrinfo", empty)
    with pytest.raises(ValueError, match="did not resolve"):
        await _public_addresses("example.com")

    async def mapped(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", mapped)
    with pytest.raises(ValueError, match="non-public"):
        await _public_addresses("example.com")

    async def public(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        del args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", public)
    assert await _public_addresses("example.com") == await public()


@pytest.mark.asyncio
async def test_pipe_tolerates_a_stream_without_half_close() -> None:
    """Closing a one-way tunnel tolerates transports without write_eof."""
    source = Mock(read=AsyncMock(return_value=b""))
    target = Mock(write_eof=Mock(side_effect=OSError))
    await _pipe(source, target)
    target.write_eof.assert_called_once()


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
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 443)),
            ]

        real_open = asyncio.open_connection

        async def pinned_open(
            host: str, port: int, **kwargs: Any
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            del kwargs
            destinations.append((host, port))
            if host == "8.8.8.8":
                raise OSError("first public address unavailable")
            return await real_open("127.0.0.1", upstream_port)

        monkeypatch.setattr(
            "orcheo.security.https_proxy._public_addresses", public_addresses
        )
        monkeypatch.setattr(asyncio, "open_connection", pinned_open)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"ping")
        await writer.drain()
        assert await reader.readexactly(4) == b"ping"
        assert destinations == [("8.8.8.8", 443), ("1.1.1.1", 443)]
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_proxy_refuses_when_public_upstream_cannot_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed public connection returns 403 without opening another target."""
    proxy = PublicHttpsProxy()
    await proxy.start()
    try:
        host, port = proxy.url.removeprefix("http://").split(":")
        reader, writer = await asyncio.open_connection(host, int(port))

        async def public_addresses(_host: str) -> list[tuple[Any, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]

        async def unavailable(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise OSError("connection refused")

        monkeypatch.setattr(
            "orcheo.security.https_proxy._public_addresses", public_addresses
        )
        monkeypatch.setattr(asyncio, "open_connection", unavailable)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.read(128)).startswith(b"HTTP/1.1 403")
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_proxy_handles_a_disconnected_client_during_refusal() -> None:
    """The proxy releases a client even if its rejection cannot be sent."""
    proxy = PublicHttpsProxy()
    reader = Mock(readuntil=AsyncMock(return_value=b"GET / HTTP/1.1\r\n\r\n"))
    writer = Mock(drain=AsyncMock(side_effect=OSError), wait_closed=AsyncMock())

    await proxy._handle(reader, writer)

    writer.write.assert_called_once()
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_proxy_can_close_before_start_and_after_shutdown() -> None:
    """Treat closing an inactive listener as an idempotent operation."""
    proxy = PublicHttpsProxy()
    await proxy.close()
    await proxy.start()
    await proxy.close()
    await proxy.close()
    with pytest.raises(RuntimeError, match="not running"):
        _ = proxy.url


@pytest.mark.asyncio
async def test_proxy_tunnel_failure_closes_both_sides_without_http_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not inject an HTTP error into an already established CONNECT tunnel."""
    reader = Mock(
        readuntil=AsyncMock(return_value=b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"),
        read=AsyncMock(side_effect=OSError("client disconnected")),
    )
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
    upstream_reader = Mock(read=AsyncMock(return_value=b""))
    upstream_writer = Mock(wait_closed=AsyncMock())
    monkeypatch.setattr(
        "orcheo.security.https_proxy._public_addresses",
        AsyncMock(
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]
        ),
    )
    monkeypatch.setattr(
        asyncio,
        "open_connection",
        AsyncMock(return_value=(upstream_reader, upstream_writer)),
    )
    await PublicHttpsProxy()._handle(reader, writer)
    writer.write.assert_called_once_with(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    upstream_writer.close.assert_called_once()
    upstream_writer.wait_closed.assert_awaited_once()
