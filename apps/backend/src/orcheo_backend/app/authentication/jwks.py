"""Generic JWKS cache for external-issuer (OIDC RP) JWT validation.

DORMANT: after the first-party auth cutover the backend validates only
first-party HS256 tokens (signed with ``AUTH_JWT_SECRET``), so this JWKS path
is never exercised unless ``AUTH_JWKS_URL`` / ``AUTH_JWKS`` is configured. It is
retained intentionally for the future enterprise-SSO initiative (OIDC/SAML
federation), which will reuse this generic relying-party layer.
"""

from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


JWKSFetcher = Callable[[], Awaitable[tuple[list[Mapping[str, Any]], int | None]]]


class JWKSCache:
    """Cache JWKS responses with respect to a configured TTL."""

    def __init__(self, fetcher: JWKSFetcher, ttl_seconds: int) -> None:
        """Initialise the cache with a JWKS fetcher and TTL."""
        self._fetcher = fetcher
        self._ttl = max(ttl_seconds, 0)
        self._lock = asyncio.Lock()
        self._jwks: list[Mapping[str, Any]] = []
        self._expires_at: datetime | None = None

    async def keys(self) -> list[Mapping[str, Any]]:
        """Return cached JWKS data, fetching when stale."""
        now = datetime.now(tz=UTC)
        if self._jwks and self._expires_at and now < self._expires_at:
            return self._jwks

        async with self._lock:
            if self._jwks and self._expires_at and now < self._expires_at:
                return self._jwks

            jwks, ttl = await self._fetcher()
            self._jwks = jwks
            effective_ttl = self._ttl
            if ttl is not None:
                header_ttl = max(ttl, 0)
                if effective_ttl > 0:
                    effective_ttl = min(effective_ttl, header_ttl)
                else:
                    effective_ttl = header_ttl
            if effective_ttl:
                self._expires_at = now + timedelta(seconds=effective_ttl)
            else:
                self._expires_at = None
            return self._jwks
