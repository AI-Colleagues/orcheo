"""Server-Side Request Forgery (SSRF) egress guard.

In restricted definition mode uploaded workflows are untrusted, yet built-in
nodes run in-process with full host network access and take their request URLs
from author-controlled config. Without an egress guard an untrusted workflow can
point a node such as :class:`~orcheo.nodes.connectors.http_request.HttpRequestNode`
at the cloud metadata endpoint (``http://169.254.169.254/``), ``localhost`` admin
ports, or other internal-only services — the classic SSRF credential-theft and
internal-network-scan vector.

This module provides a reusable guard that:

* permits only ``http`` / ``https`` URLs, and
* resolves the target host and rejects the request when **any** resolved address
  is loopback, link-local, private (RFC 1918 / ULA), carrier-grade NAT,
  multicast, reserved, or otherwise non-global.

Validation runs *after* name resolution, so obfuscated literals (decimal / octal
/ hex IPs, IPv4-mapped IPv6, ``0.0.0.0``) are normalised by the resolver and
caught by the same address check. :class:`SSRFGuardAsyncTransport` re-runs the
check for every hop of an httpx redirect chain, closing redirect-based bypasses;
httpx invokes the transport once per hop and an exception raised there aborts the
chain before the disallowed host is contacted.

Callers gate on :func:`~orcheo.graph.ir.definition_mode.is_restricted_mode`, so
trusted/self-hosted deployments keep unrestricted egress. The residual
DNS-rebinding window (a resolver returning a public address to the guard and a
private one to the connecting socket) is not closed here; pin resolution upstream
if that threat is in scope.
"""

from __future__ import annotations
import asyncio
import ipaddress
import socket
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit
import httpx


# URL schemes an SSRF-guarded request may use.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Default ports assumed when a URL omits one, keyed by scheme.
_DEFAULT_PORTS = {"http": 80, "https": 443}


class SSRFError(ValueError):
    """Raised when a request target is disallowed by the SSRF egress guard."""


def _default_port(scheme: str) -> int:
    """Return the default TCP port for a validated URL scheme."""
    return _DEFAULT_PORTS.get(scheme, 0)


def _split_target(url: str) -> tuple[str, int]:
    """Return the ``(host, port)`` of a URL, rejecting unusable targets.

    Raises:
        SSRFError: When the scheme is not ``http``/``https`` or the host is
            missing or malformed.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise SSRFError(f"could not parse request URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(
            f"URL scheme '{parts.scheme or '(none)'}' is not permitted; only "
            "http and https requests are allowed in restricted mode"
        )

    host = parts.hostname
    if not host:
        raise SSRFError("request URL must include a host")

    try:
        port = parts.port
    except ValueError as exc:
        raise SSRFError(f"request URL has an invalid port: {exc}") from exc

    return host, port if port is not None else _default_port(scheme)


def _blocked_reason(address: str) -> str | None:
    """Return why ``address`` is a blocked egress target, or ``None`` if allowed.

    IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``) are evaluated by their
    embedded IPv4 address so a mapped loopback/private address cannot slip past
    as a "global" IPv6 address.
    """
    # getaddrinfo may append an IPv6 scope id (``fe80::1%eth0``) that
    # ipaddress cannot parse; drop it before classifying.
    ip_obj = ipaddress.ip_address(address.split("%", 1)[0])
    if ip_obj.version == 6 and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    # Ordered most-specific first so the error names the tightest category; the
    # generic non-global check is the catch-all backstop (e.g. carrier-grade NAT).
    checks = (
        (ip_obj.is_loopback, "a loopback"),
        (ip_obj.is_link_local, "a link-local"),
        (ip_obj.is_multicast, "a multicast"),
        (ip_obj.is_unspecified, "an unspecified"),
        (ip_obj.is_private, "a private"),
        (ip_obj.is_reserved, "a reserved"),
        (not ip_obj.is_global, "a non-global"),
    )
    for is_blocked, description in checks:
        if is_blocked:
            return f"{ip_obj} is {description} address"
    return None


def _check_resolved(host: str, resolved: Sequence[Any]) -> None:
    """Reject the host when any resolved address is a blocked egress target.

    ``resolved`` is a ``socket.getaddrinfo`` result: a sequence of
    ``(family, type, proto, canonname, sockaddr)`` tuples whose ``sockaddr[0]``
    is the resolved IP address string.

    Raises:
        SSRFError: On the first address that resolves to a non-global target, or
            when resolution yielded no usable addresses.
    """
    addresses = {str(entry[4][0]) for entry in resolved if entry[4]}
    if not addresses:
        raise SSRFError(f"host '{host}' did not resolve to any address")
    for address in sorted(addresses):
        reason = _blocked_reason(address)
        if reason is not None:
            raise SSRFError(
                f"host '{host}' resolves to {reason}, which is not an allowed "
                "request target in restricted mode"
            )


def validate_public_url(url: str) -> None:
    """Validate that ``url`` targets a public host (synchronous resolver).

    Raises:
        SSRFError: When the scheme is disallowed, the host is missing, resolution
            fails, or any resolved address is a non-global target.
    """
    host, port = _split_target(url)
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"could not resolve host '{host}': {exc}") from exc
    _check_resolved(host, resolved)


async def validate_public_url_async(url: str) -> None:
    """Validate that ``url`` targets a public host using the event loop resolver.

    Raises:
        SSRFError: When the scheme is disallowed, the host is missing, resolution
            fails, or any resolved address is a non-global target.
    """
    host, port = _split_target(url)
    await validate_public_host_async(host, port)


async def validate_public_host_async(host: str, port: int) -> None:
    """Validate that a raw TCP host resolves only to public addresses.

    This supports non-HTTP clients such as SMTP, where the URL-oriented guard
    cannot be installed as an ``httpx`` transport.

    Raises:
        SSRFError: When the host is missing, resolution fails, or any resolved
            address is a non-global target.
    """
    if not host:
        raise SSRFError("request target must include a host")
    loop = asyncio.get_running_loop()
    try:
        resolved = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"could not resolve host '{host}': {exc}") from exc
    _check_resolved(host, resolved)


async def validate_restricted_egress_host_async(host: str, port: int) -> None:
    """Validate a non-HTTP target when restricted definition mode is active."""
    from orcheo.graph.ir.definition_mode import is_restricted_mode

    if is_restricted_mode():
        await validate_public_host_async(host, port)


class SSRFGuardAsyncTransport(httpx.AsyncHTTPTransport):
    """httpx transport that validates every request (and redirect hop) target.

    httpx calls :meth:`handle_async_request` once per hop while following
    redirects, so validating here rejects a redirect to an internal address
    before the connection to it is made.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Validate the request URL, then delegate to the base transport."""
        await validate_public_url_async(str(request.url))
        return await super().handle_async_request(request)


def restricted_egress_client_kwargs() -> dict[str, Any]:
    """Return ``httpx.AsyncClient`` kwargs that add the SSRF guard when needed.

    In restricted definition mode the workflow author is untrusted, so built-in
    nodes that make author-configured outbound requests install the SSRF-guarded
    transport (which validates the initial request and every redirect hop).
    Trusted/unrestricted deployments get an empty mapping and keep unrestricted
    egress.
    """
    # Imported lazily to avoid importing the settings stack at module load.
    from orcheo.graph.ir.definition_mode import is_restricted_mode

    if is_restricted_mode():
        return {"transport": SSRFGuardAsyncTransport()}
    return {}


__all__ = [
    "SSRFError",
    "SSRFGuardAsyncTransport",
    "restricted_egress_client_kwargs",
    "validate_public_host_async",
    "validate_public_url",
    "validate_public_url_async",
    "validate_restricted_egress_host_async",
]
