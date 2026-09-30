"""Exercise real HTTPX/httpcore request routing without opening sockets."""

from __future__ import annotations
import asyncio
import socket
import ssl
from typing import Any
import httpcore
import httpx
import pytest
from orcheo.security.ssrf import (
    PublicNetworkBackend,
    SSRFError,
    SSRFGuardAsyncTransport,
    public_https_client_kwargs,
)


def _answer(address: str, port: int) -> list[Any]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, port))]


@pytest.mark.asyncio
async def test_rebinding_is_rejected_at_actual_connection(monkeypatch: Any) -> None:
    """A public preflight followed by private DNS never reaches the socket."""
    answers = iter(["8.8.8.8", "127.0.0.1"])
    destinations = []

    async def resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        return _answer(next(answers), port)

    async def connect(self: Any, host: str, port: int, **kwargs: Any) -> Any:
        destinations.append((host, port))
        raise AssertionError("A private connection must never be opened")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    async with httpx.AsyncClient(**public_https_client_kwargs()) as client:
        with pytest.raises(SSRFError):
            await client.get("https://rebind.example/cover.jpg")
    assert destinations == []


@pytest.mark.asyncio
async def test_pinned_connection_preserves_host_and_verified_tls(
    monkeypatch: Any,
) -> None:
    """Only numeric IPs connect; TLS and HTTP still use the original hostname."""
    dns_calls = []
    destinations = []
    tls_hosts = []
    written = []

    class Stream(httpcore.AsyncMockStream):
        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            written.append(buffer)
            await super().write(buffer, timeout=timeout)

        async def start_tls(
            self,
            ssl_context: Any,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> Any:
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname
            tls_hosts.append(server_hostname)
            return self

    stream = Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nimage"])

    async def resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        dns_calls.append(host)
        return _answer("8.8.8.8", port)

    async def connect(self: Any, host: str, port: int, **kwargs: Any) -> Any:
        destinations.append((host, port))
        return stream

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    async with httpx.AsyncClient(**public_https_client_kwargs()) as client:
        response = await client.get("https://images.example/cover.jpg")
    assert response.content == b"image"
    assert destinations == [("8.8.8.8", 443)]
    assert len(dns_calls) == 2
    assert tls_hosts == ["images.example"]
    assert b"Host: images.example" in b"".join(written)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    [
        "https://127.0.0.1/admin",
        "https://169.254.169.254/",
        "http://public.example/",
        "https://public.example:8443/",
    ],
)
async def test_redirect_chain_refuses_disallowed_hops(
    monkeypatch: Any, destination: str
) -> None:
    """A redirect cannot downgrade HTTPS or contact an internal service."""
    connections = []

    async def resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        return _answer(
            host if host in {"127.0.0.1", "169.254.169.254"} else "8.8.8.8", port
        )

    async def connect(self: Any, host: str, port: int, **kwargs: Any) -> Any:
        connections.append(host)
        return httpcore.AsyncMockStream(
            [
                f"HTTP/1.1 302 Found\r\nLocation: {destination}\r\nContent-Length: 0\r\n\r\n".encode()
            ]
        )

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    async with httpx.AsyncClient(
        follow_redirects=True, **public_https_client_kwargs()
    ) as client:
        with pytest.raises(SSRFError):
            await client.get("https://public.example/")
    assert connections == ["8.8.8.8"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address", ["224.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::", "::ffff:127.0.0.1"]
)
async def test_special_addresses_cannot_bypass_connection_guard(
    monkeypatch: Any, address: str
) -> None:
    async def resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        return _answer(address, port)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(SSRFError):
        await PublicNetworkBackend().connect_tcp("special.example", 443)


def test_unverified_tls_is_not_an_option() -> None:
    with pytest.raises(ValueError, match="verification"):
        SSRFGuardAsyncTransport(ssl_context=ssl._create_unverified_context())
