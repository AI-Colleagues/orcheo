"""JWT authentication helpers."""

from __future__ import annotations
import logging
from collections.abc import Mapping, Sequence
from typing import Any
import httpx
import jwt
from fastapi import status
from jwt import PyJWK, PyJWKError
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
)
from .context import RequestContext
from .errors import AuthenticationError
from .jwks import JWKSCache
from .jwt_helpers import _parse_max_age, claims_to_context
from .settings import AuthSettings


logger = logging.getLogger(__name__)


class JWTAuthenticator:
    """Validate JWT bearer tokens using secrets or JWKS configuration."""

    def __init__(self, settings: AuthSettings) -> None:
        """Prepare caches and static keys using the provided settings."""
        self._settings = settings
        self._jwks_cache: JWKSCache | None = None
        if settings.jwks_url:
            self._jwks_cache = JWKSCache(self._fetch_jwks, settings.jwks_cache_ttl)
        self._static_jwks: list[tuple[PyJWK, str | None]] = []
        for entry in settings.jwks_static:
            try:
                jwk = PyJWK.from_dict(dict(entry))
            except PyJWKError as exc:  # pragma: no cover - defensive
                logger.warning("Invalid JWKS entry skipped: %s", exc)
                continue
            algorithm_hint = entry.get("alg") if isinstance(entry, Mapping) else None
            algorithm_str = algorithm_hint if isinstance(algorithm_hint, str) else None
            self._static_jwks.append((jwk, algorithm_str))
        self._resolve_signing_key_func = self._resolve_signing_key_impl

    @property
    def configured(self) -> bool:
        """Return True when JWT authentication inputs are available."""
        return bool(
            self._settings.jwt_secret
            or self._settings.jwks_url
            or self._settings.jwks_static
        )

    @property
    def jwks_cache(self) -> JWKSCache | None:
        """Expose the JWKS cache so callers can override it."""
        return self._jwks_cache

    @jwks_cache.setter
    def jwks_cache(self, cache: JWKSCache | None) -> None:
        self._jwks_cache = cache

    @property
    def static_jwks(self) -> list[tuple[PyJWK, str | None]]:
        """Return the configured static JWKS entries."""
        return self._static_jwks

    async def authenticate(self, token: str) -> RequestContext:
        """Validate ``token`` and return a populated request context."""
        header = self._extract_header(token)
        key = await self._select_signing_key(header)
        claims = self._decode_claims(token, key)
        return claims_to_context(claims)

    def _extract_header(self, token: str) -> Mapping[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:  # pragma: no cover - defensive
            message = "Invalid bearer token"
            raise AuthenticationError(message, code="auth.invalid_token") from exc

        algorithm = header.get("alg")
        allowed = self._settings.allowed_algorithms
        if allowed and algorithm and algorithm not in allowed:
            message = "Bearer token is signed with an unsupported algorithm"
            raise AuthenticationError(message, code="auth.unsupported_algorithm")
        return header

    async def _select_signing_key(self, header: Mapping[str, Any]) -> Any:
        algorithm = header.get("alg")
        if (
            self._settings.jwt_secret
            and isinstance(algorithm, str)
            and algorithm.startswith("HS")
        ):
            return self._settings.jwt_secret

        key = await self._resolve_signing_key(header)
        if key is None:
            message = "Unable to resolve signing key for bearer token"
            raise AuthenticationError(
                message,
                code="auth.key_unavailable",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return key

    def _decode_claims(self, token: str, key: Any) -> Mapping[str, Any]:
        decode_args: dict[str, Any] = {
            "algorithms": self._settings.allowed_algorithms or None,
            "options": {"verify_aud": bool(self._settings.audiences)},
        }
        if self._settings.audiences:
            decode_args["audience"] = list(self._settings.audiences)

        try:
            claims = jwt.decode(token, key, **decode_args)
        except ExpiredSignatureError as exc:
            raise AuthenticationError(
                "Bearer token has expired",
                code="auth.token_expired",
            ) from exc
        except InvalidAudienceError as exc:
            raise AuthenticationError(
                "Bearer token has an invalid audience",
                code="auth.invalid_audience",
                status_code=status.HTTP_403_FORBIDDEN,
            ) from exc
        except InvalidIssuerError as exc:
            raise AuthenticationError(
                "Bearer token has an invalid issuer",
                code="auth.invalid_issuer",
                status_code=status.HTTP_403_FORBIDDEN,
            ) from exc
        except InvalidTokenError as exc:
            raise AuthenticationError(
                "Invalid bearer token",
                code="auth.invalid_token",
            ) from exc

        self._validate_issuer(claims)
        return claims

    def _validate_issuer(self, claims: Mapping[str, Any]) -> None:
        """Validate the issuer claim using a normalized URL comparison."""
        expected = self._settings.issuer
        if not expected:
            return

        issuer = claims.get("iss")
        if not isinstance(issuer, str):
            raise AuthenticationError(
                "Bearer token has an invalid issuer",
                code="auth.invalid_issuer",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if issuer.rstrip("/") != expected.rstrip("/"):
            raise AuthenticationError(
                "Bearer token has an invalid issuer",
                code="auth.invalid_issuer",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    async def _resolve_signing_key(self, header: Mapping[str, Any]) -> Any | None:
        return await self._resolve_signing_key_func(header)

    async def _resolve_signing_key_impl(self, header: Mapping[str, Any]) -> Any | None:
        kid = header.get("kid")
        algorithm = header.get("alg")
        key = self._match_static_key(kid, algorithm)
        if key is not None:
            return key

        if not self._jwks_cache:
            return None

        jwks = await self._jwks_cache.keys()
        return self._match_fetched_key(jwks, kid, algorithm)

    def _match_static_key(self, kid: Any, algorithm: Any) -> Any | None:
        for jwk, jwk_algorithm in self._static_jwks:
            if kid and jwk.key_id != kid:
                continue
            if algorithm and jwk_algorithm and jwk_algorithm != algorithm:
                continue
            return jwk.key
        return None

    def _match_fetched_key(
        self, entries: Sequence[Mapping[str, Any]], kid: Any, algorithm: Any
    ) -> Any | None:
        for entry in entries:
            try:
                jwk = PyJWK.from_dict(dict(entry))
            except PyJWKError:  # pragma: no cover - invalid JWKS entries are skipped
                continue
            if kid and jwk.key_id != kid:
                continue
            entry_algorithm = entry.get("alg") if isinstance(entry, Mapping) else None
            algorithm_hint = (
                entry_algorithm if isinstance(entry_algorithm, str) else None
            )
            if algorithm and algorithm_hint and algorithm_hint != algorithm:
                continue
            return jwk.key
        return None

    async def _fetch_jwks(self) -> tuple[list[Mapping[str, Any]], int | None]:
        if not self._settings.jwks_url:
            return [], None

        async with httpx.AsyncClient(timeout=self._settings.jwks_timeout) as client:
            response = await client.get(self._settings.jwks_url)
        response.raise_for_status()
        data = response.json()
        keys = data.get("keys", []) if isinstance(data, Mapping) else []
        ttl = _parse_max_age(response.headers.get("Cache-Control"))
        return [dict(item) for item in keys if isinstance(item, Mapping)], ttl


__all__ = ["JWTAuthenticator"]
