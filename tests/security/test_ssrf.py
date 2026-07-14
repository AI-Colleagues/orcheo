"""Tests for the SSRF egress guard."""

from __future__ import annotations
import socket
from typing import Any
import httpx
import pytest
import orcheo.security.ssrf as ssrf
from orcheo.security.ssrf import (
    SSRFError,
    SSRFGuardAsyncTransport,
    restricted_egress_client_kwargs,
    validate_public_host_async,
    validate_public_url,
    validate_public_url_async,
    validate_restricted_egress_host_async,
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


def test_validate_public_url_rejects_invalid_port() -> None:
    """A URL with a non-numeric port is rejected before DNS resolution."""
    with pytest.raises(SSRFError, match="invalid port"):
        validate_public_url("http://example.com:not-a-port/")


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


def test_validate_public_url_rejects_empty_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosts with no usable DNS answers fail closed."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(SSRFError, match="did not resolve"):
        validate_public_url("http://empty.example.test/")


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
async def test_validate_public_host_async_blocks_internal_smtp_target() -> None:
    """The raw-host validator blocks internal targets for non-HTTP clients."""
    with pytest.raises(SSRFError):
        await validate_public_host_async("127.0.0.1", 25)


@pytest.mark.asyncio
async def test_validate_public_host_async_allows_public_smtp_target() -> None:
    """The raw-host validator permits public targets for non-HTTP clients."""
    await validate_public_host_async("8.8.8.8", 25)


@pytest.mark.asyncio
async def test_validate_public_host_async_requires_host() -> None:
    """The raw-host validator rejects an empty hostname."""
    with pytest.raises(SSRFError, match="must include a host"):
        await validate_public_host_async("", 25)


@pytest.mark.asyncio
async def test_validate_public_host_async_rejects_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async DNS failures fail closed with the original resolution error."""

    class _FailingLoop:
        async def getaddrinfo(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise socket.gaierror("async name resolution failed")

    monkeypatch.setattr(ssrf.asyncio, "get_running_loop", lambda: _FailingLoop())
    with pytest.raises(SSRFError, match="could not resolve host"):
        await validate_public_host_async("missing.example.test", 25)


@pytest.mark.asyncio
async def test_validate_restricted_egress_host_async_validates_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restricted mode delegates raw-host validation to the public-host guard."""
    calls: list[tuple[str, int]] = []

    async def _validate(host: str, port: int) -> None:
        calls.append((host, port))

    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: True
    )
    monkeypatch.setattr(ssrf, "validate_public_host_async", _validate)

    await validate_restricted_egress_host_async("smtp.example.test", 587)

    assert calls == [("smtp.example.test", 587)]


@pytest.mark.asyncio
async def test_validate_restricted_egress_host_async_skips_validation_when_unrestricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrestricted mode leaves non-HTTP egress unchanged."""
    called = False

    async def _validate(host: str, port: int) -> None:
        nonlocal called
        del host, port
        called = True

    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: False
    )
    monkeypatch.setattr(ssrf, "validate_public_host_async", _validate)

    await validate_restricted_egress_host_async("127.0.0.1", 25)

    assert not called


def test_restricted_egress_client_kwargs_installs_guard_in_restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restricted mode supplies a transport that guards every HTTP hop."""
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: True
    )

    kwargs = restricted_egress_client_kwargs()

    assert isinstance(kwargs["transport"], SSRFGuardAsyncTransport)


def test_restricted_egress_client_kwargs_is_empty_when_unrestricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrestricted mode does not alter the HTTP client configuration."""
    monkeypatch.setattr(
        "orcheo.graph.ir.definition_mode.is_restricted_mode", lambda: False
    )

    assert restricted_egress_client_kwargs() == {}


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


@pytest.mark.asyncio
async def test_guard_transport_delegates_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validated request is passed to the underlying HTTP transport."""

    async def _validate(url: str) -> None:
        assert url == "https://example.com/"

    async def _handle(
        transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        del transport
        return httpx.Response(204, request=request)

    monkeypatch.setattr(ssrf, "validate_public_url_async", _validate)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _handle)

    transport = SSRFGuardAsyncTransport()
    try:
        response = await transport.handle_async_request(
            httpx.Request("GET", "https://example.com/")
        )
    finally:
        await transport.aclose()

    assert response.status_code == 204
