"""Tests for the SSRF egress guard."""

from __future__ import annotations
import socket
from typing import Any
import httpx
import pytest
from orcheo.security.ssrf import (
    SSRFError,
    SSRFGuardAsyncTransport,
    validate_public_url,
    validate_public_url_async,
)


# Numeric hosts resolve without a DNS query, so these cases stay offline.
BLOCKED_LITERAL_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1:8080/admin",  # loopback
    "http://10.0.0.5/",  # RFC 1918
    "http://192.168.1.1/",  # RFC 1918
    "http://172.16.0.1/",  # RFC 1918
    "http://100.64.0.1/",  # carrier-grade NAT
    "http://0.0.0.0/",  # unspecified
    "http://224.0.0.1/",  # multicast
    "http://[::1]/",  # IPv6 loopback
    "http://[fd00::1]/",  # IPv6 ULA
    "http://[fe80::1]/",  # IPv6 link-local
    "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
]

ALLOWED_LITERAL_URLS = [
    "http://8.8.8.8/",
    "https://1.1.1.1/resolve",
    "http://[2606:4700:4700::1111]/",
    "http://[::ffff:8.8.8.8]/",  # IPv4-mapped public
]

REJECTED_SCHEME_URLS = [
    "ftp://8.8.8.8/file",
    "file:///etc/passwd",
    "gopher://8.8.8.8/",
    "//8.8.8.8/no-scheme",
]


@pytest.mark.parametrize("url", BLOCKED_LITERAL_URLS)
def test_validate_public_url_blocks_internal_literals(url: str) -> None:
    """Requests to internal/non-global IP literals are rejected."""
    with pytest.raises(SSRFError):
        validate_public_url(url)


@pytest.mark.parametrize("url", ALLOWED_LITERAL_URLS)
def test_validate_public_url_allows_public_literals(url: str) -> None:
    """Requests to public IP literals pass validation."""
    validate_public_url(url)


@pytest.mark.parametrize("url", REJECTED_SCHEME_URLS)
def test_validate_public_url_rejects_non_http_schemes(url: str) -> None:
    """Only http/https schemes are permitted."""
    with pytest.raises(SSRFError, match="scheme"):
        validate_public_url(url)


def test_validate_public_url_requires_host() -> None:
    """A URL without a host is rejected."""
    with pytest.raises(SSRFError, match="host"):
        validate_public_url("http:///path-only")


def _fake_getaddrinfo(*addresses: str) -> Any:
    """Return a ``socket.getaddrinfo`` stub yielding the given IP addresses."""

    def _resolver(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        del host, port, args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))
            for address in addresses
        ]

    return _resolver


def test_validate_public_url_blocks_hostname_resolving_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname that resolves to a private address is rejected (internal DNS)."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
    with pytest.raises(SSRFError, match="private"):
        validate_public_url("http://internal.example.test/")


def test_validate_public_url_blocks_when_any_address_is_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If any resolved address is internal, the request is rejected."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8", "127.0.0.1")
    )
    with pytest.raises(SSRFError):
        validate_public_url("http://rebind.example.test/")


def test_validate_public_url_allows_hostname_resolving_to_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname resolving only to public addresses is allowed."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    validate_public_url("https://example.com/")


def test_validate_public_url_rejects_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolvable hosts fail closed rather than being allowed through."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(SSRFError, match="resolve"):
        validate_public_url("http://does-not-exist.example.test/")


@pytest.mark.asyncio
async def test_validate_public_url_async_blocks_internal() -> None:
    """The async validator rejects internal literals."""
    with pytest.raises(SSRFError):
        await validate_public_url_async("http://169.254.169.254/")


@pytest.mark.asyncio
async def test_validate_public_url_async_allows_public() -> None:
    """The async validator accepts public literals."""
    await validate_public_url_async("http://8.8.8.8/")


@pytest.mark.asyncio
async def test_guard_transport_rejects_internal_hop() -> None:
    """The guarded transport blocks an internal target before connecting.

    This is the redirect-hop enforcement point: httpx calls the transport for
    each hop, so raising here aborts a redirect chain before the internal host
    is contacted.
    """
    transport = SSRFGuardAsyncTransport()
    request = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
    try:
        with pytest.raises(SSRFError):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()
