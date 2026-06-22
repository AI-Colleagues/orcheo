"""Token primitives for the first-party identity service.

Magic-link tokens, OTP codes, and refresh tokens are random secrets that are
emailed/returned to the user once and only ever persisted as SHA-256 hashes.
Access tokens are short-lived HS256 JWTs carrying the existing Orcheo claim
contract validated by ``authentication/``.
"""

from __future__ import annotations
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
import jwt
from orcheo.identity.models import User


__all__ = [
    "generate_magic_link_token",
    "generate_otp_code",
    "generate_refresh_token",
    "hash_secret",
    "mint_access_token",
    "secrets_match",
]

_MAGIC_LINK_BYTES = 32
_REFRESH_TOKEN_BYTES = 32


def generate_magic_link_token() -> str:
    """Return a URL-safe single-use magic-link token."""
    return secrets.token_urlsafe(_MAGIC_LINK_BYTES)


def generate_refresh_token() -> str:
    """Return a URL-safe single-use refresh token."""
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def generate_otp_code(digits: int = 6) -> str:
    """Return a zero-padded numeric OTP code of ``digits`` length."""
    if digits < 4:
        msg = "OTP codes must have at least 4 digits."
        raise ValueError(msg)
    upper = 10**digits
    return str(secrets.randbelow(upper)).zfill(digits)


def hash_secret(raw: str) -> str:
    """Return the hex SHA-256 of a raw secret for storage/comparison."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def secrets_match(raw: str, expected_hash: str) -> bool:
    """Return True when ``raw`` hashes to ``expected_hash`` (constant-time)."""
    return hmac.compare_digest(hash_secret(raw), expected_hash)


def mint_access_token(
    *,
    user: User,
    secret: str,
    issuer: str,
    audience: str | None,
    ttl_seconds: int,
    now: datetime,
    extra_claims: Mapping[str, Any] | None = None,
) -> tuple[str, int]:
    """Mint an HS256 access token for ``user`` and return ``(token, expires_in)``.

    The claim set matches the contract validated by ``authentication/``:
    ``sub`` is the internal user id, plus ``email`` / ``email_verified`` /
    ``name`` and the standard ``iss`` / ``aud`` / ``iat`` / ``exp``.
    """
    expires_at = now + timedelta(seconds=ttl_seconds)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "email_verified": user.email_verified,
        "name": user.name,
        "iss": issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if audience:
        claims["aud"] = audience
    if extra_claims:
        claims.update(extra_claims)
    token = jwt.encode(claims, secret, algorithm="HS256")
    return token, ttl_seconds


def coerce_user_id(value: str | UUID) -> UUID:
    """Return ``value`` as a UUID, accepting the string ``sub`` form."""
    return value if isinstance(value, UUID) else UUID(str(value))
