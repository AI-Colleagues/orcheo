"""Resolved configuration for the first-party identity service."""

from __future__ import annotations
from dataclasses import dataclass


__all__ = ["IdentityConfig", "DEFAULT_FIRST_PARTY_ISSUER"]

DEFAULT_FIRST_PARTY_ISSUER = "https://auth.orcheo.cloud"


@dataclass(frozen=True)
class IdentityConfig:
    """Tunables for challenge issuance, verification, and token minting."""

    jwt_secret: str
    issuer: str = DEFAULT_FIRST_PARTY_ISSUER
    audience: str | None = None
    access_ttl_seconds: int = 900
    challenge_ttl_minutes: int = 15
    session_ttl_days: int = 30
    otp_digits: int = 6
    otp_max_attempts: int = 5
    verify_base_url: str = "http://localhost:2026"
