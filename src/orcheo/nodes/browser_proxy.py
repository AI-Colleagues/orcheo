"""A connection-time public HTTPS egress boundary for untrusted browser pages."""

from __future__ import annotations
import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit


_HEADER_LIMIT = 16_384
_CONNECT_TIMEOUT = 10


def _connect_host(authority: str) -> str:
    """Accept only an HTTPS CONNECT authority on port 443."""
    if any(char in authority for char in "/@?# "):
        raise ValueError("invalid CONNECT authority")
    parsed = urlsplit(f"//{authority}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid CONNECT port") from error
    if not parsed.hostname or port != 443 or parsed.netloc != authority:
        raise ValueError("CONNECT requires a host on port 443")
    return parsed.hostname


async def _public_addresses(host: str) -> list[tuple[Any, ...]]:
    """Resolve once and return only addresses whose entire answer is public."""
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM),
        _CONNECT_TIMEOUT,
    )
    if not infos:
        raise ValueError("CONNECT hostname did not resolve")
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global:
            raise ValueError("CONNECT hostname resolved to a non-public address")
    return infos


async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    """Copy bytes until one tunnel direction closes."""
    try:
        while chunk := await source.read(65_536):
            target.write(chunk)
            await target.drain()
    finally:
        try:
            target.write_eof()
        except (AttributeError, OSError, RuntimeError):
            pass


class PublicHttpsProxy:
    """Pin each browser CONNECT to a checked public IP, including redirects."""

    def __init__(self) -> None:
        """Initialize a listener that starts on demand."""
        self._server: asyncio.Server | None = None

    @property
    def url(self) -> str:
        """Return the loopback proxy address after start."""
        if self._server is None or not self._server.sockets:
            raise RuntimeError("public HTTPS proxy is not running")
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def start(self) -> None:
        """Bind a private listener used by one browser session."""
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0, limit=_HEADER_LIMIT
        )

    async def close(self) -> None:
        """Stop accepting new browser connections."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream: asyncio.StreamWriter | None = None
        connected = False
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), _CONNECT_TIMEOUT
            )
            request_line = header.split(b"\r\n", 1)[0].decode("ascii")
            method, authority, version = request_line.split(" ")
            if method != "CONNECT" or version != "HTTP/1.1":
                raise ValueError("only HTTPS CONNECT is allowed")
            host = _connect_host(authority)
            addresses = await _public_addresses(host)
            for family, _, _, _, sockaddr in addresses:
                try:
                    upstream_reader, upstream = await asyncio.wait_for(
                        asyncio.open_connection(sockaddr[0], 443, family=family),
                        _CONNECT_TIMEOUT,
                    )
                    break
                except (OSError, TimeoutError):
                    continue
            if upstream is None:
                raise OSError("no public destination accepted the connection")
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            connected = True
            await asyncio.gather(
                _pipe(reader, upstream), _pipe(upstream_reader, writer)
            )
        except (
            ValueError,
            UnicodeError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            if not connected:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
                await upstream.wait_closed()
            writer.close()
            await writer.wait_closed()
