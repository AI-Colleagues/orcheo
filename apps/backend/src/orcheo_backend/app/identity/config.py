"""Resolved configuration for the first-party identity service."""

from __future__ import annotations
import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit


__all__ = [
    "IdentityConfig",
    "DEFAULT_FIRST_PARTY_ISSUER",
    "resolve_passkey_relying_party",
]

logger = logging.getLogger(__name__)

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
    # Exact email domains allowed to sign in; empty allows every domain.
    allowed_email_domains: tuple[str, ...] = ()
    # Passkey (WebAuthn) relying party ID; ``None`` turns passkeys off.
    webauthn_rp_id: str | None = None
    webauthn_rp_name: str = "Orcheo"
    # Exact browser origins (Studio's) allowed to run passkey ceremonies.
    webauthn_origins: tuple[str, ...] = ()
    passkey_challenge_ttl_seconds: int = 300
    # How recently a user must have signed in to add a passkey.
    passkey_reauth_max_age_seconds: int = 600


def resolve_passkey_relying_party(
    studio_url: str,
    *,
    rp_id: str | None = None,
    origins: str | Iterable[str] | None = None,
) -> tuple[str | None, tuple[str, ...]]:
    """Return the passkey RP ID and allowed origins, or ``(None, ())``.

    The browser reports the origin of the page that runs the ceremony, which
    is Studio's, so origins default to the Studio origin and the RP ID to its
    host. Browsers only run passkey ceremonies in secure contexts (https, or
    http on localhost) and never for IP addresses, so such configurations turn
    passkeys off instead of failing every ceremony. Origins are matched
    exactly, never by suffix: other subdomains of the RP ID (such as hosted
    apps) must not be able to sign users in.
    """
    entries = _split_entries(origins) or [studio_url]
    try:
        allowed = tuple(dict.fromkeys(_normalize_origin(entry) for entry in entries))
    except ValueError as exc:
        logger.warning("Passkeys are disabled: %s", exc)
        return None, ()
    hosts = [str(urlsplit(origin).hostname) for origin in allowed]
    candidate = (rp_id or "").strip().lower().rstrip(".") or hosts[0]
    if _is_ip_address(candidate):
        logger.warning("Passkeys are disabled: RP ID %r is an IP address.", candidate)
        return None, ()
    outside = [h for h in hosts if h != candidate and not h.endswith(f".{candidate}")]
    if outside:
        logger.warning(
            "Passkeys are disabled: origin host %r is not within RP ID %r.",
            outside[0],
            candidate,
        )
        return None, ()
    return candidate, allowed


def _split_entries(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    return [entry for entry in (str(item).strip() for item in items) if entry]


def _normalize_origin(value: str) -> str:
    """Serialize ``value`` the way browsers report an origin."""
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        msg = f"{value!r} is not an http(s) URL."
        raise ValueError(msg)
    if scheme == "http" and host != "localhost" and not host.endswith(".localhost"):
        msg = f"{value!r} is not served over https (or from localhost)."
        raise ValueError(msg)
    port = parts.port
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    return f"{scheme}://{netloc}"


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
