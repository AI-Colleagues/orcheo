"""FastAPI dependencies wiring the first-party identity service."""

from __future__ import annotations
from typing import Annotated
from fastapi import Depends, Request
from orcheo.config import get_settings
from orcheo.identity import (
    IdentityRepository,
    InMemoryIdentityRepository,
    PostgresIdentityRepository,
)
from orcheo_backend.app.authentication.settings import load_auth_settings
from orcheo_backend.app.email_config import build_transactional_email_sender
from orcheo_backend.app.identity.config import (
    DEFAULT_FIRST_PARTY_ISSUER,
    IdentityConfig,
)
from orcheo_backend.app.identity.service import IdentityService


TRUTHY_VALUES = {"1", "true", "yes", "on"}


__all__ = [
    "IdentityServiceDep",
    "get_client_ip",
    "get_identity_config",
    "get_identity_repository",
    "get_identity_service",
    "reset_identity_state",
    "set_identity_repository",
    "set_identity_service",
]


_identity_repository_ref: dict[str, IdentityRepository | None] = {"repository": None}
_identity_service_ref: dict[str, IdentityService | None] = {"service": None}


def set_identity_repository(repository: IdentityRepository | None) -> None:
    """Override the identity repository singleton (primarily for testing)."""
    _identity_repository_ref["repository"] = repository
    _identity_service_ref["service"] = None


def set_identity_service(service: IdentityService | None) -> None:
    """Override the identity service singleton (primarily for testing)."""
    _identity_service_ref["service"] = service
    if service is not None:
        _identity_repository_ref["repository"] = service.repository


def reset_identity_state() -> None:
    """Drop cached identity singletons; refreshes settings."""
    _identity_repository_ref["repository"] = None
    _identity_service_ref["service"] = None
    get_settings(refresh=True)


def get_identity_repository() -> IdentityRepository:
    """Return the configured identity repository."""
    repository = _identity_repository_ref.get("repository")
    if repository is None:
        settings = get_settings()
        backend = str(settings.get("WORKSPACE_BACKEND", "postgres")).lower()
        dsn = settings.get("POSTGRES_DSN")
        if backend == "postgres" and dsn:
            repository = PostgresIdentityRepository(str(dsn))
        else:
            repository = InMemoryIdentityRepository()
        _identity_repository_ref["repository"] = repository
    return repository


def get_identity_config() -> IdentityConfig:
    """Build identity tunables from auth and app settings."""
    auth_settings = load_auth_settings()
    settings = get_settings()
    jwt_secret = auth_settings.jwt_secret
    if not jwt_secret:
        msg = "AUTH_JWT_SECRET must be set to issue first-party tokens."
        raise ValueError(msg)
    issuer = auth_settings.issuer or DEFAULT_FIRST_PARTY_ISSUER
    audience = auth_settings.audiences[0] if auth_settings.audiences else None
    verify_base_url = str(settings.get("STUDIO_URL") or "http://localhost:2026")
    return IdentityConfig(
        jwt_secret=jwt_secret,
        issuer=issuer,
        audience=audience,
        access_ttl_seconds=int(settings.get("AUTH_ACCESS_TOKEN_TTL_SECONDS") or 900),
        challenge_ttl_minutes=int(settings.get("AUTH_CHALLENGE_TTL_MINUTES") or 15),
        session_ttl_days=int(settings.get("AUTH_SESSION_TTL_DAYS") or 30),
        otp_digits=int(settings.get("AUTH_OTP_DIGITS") or 6),
        otp_max_attempts=int(settings.get("AUTH_OTP_MAX_ATTEMPTS") or 5),
        verify_base_url=verify_base_url,
    )


def get_identity_service() -> IdentityService:
    """Return the cached identity service singleton."""
    service = _identity_service_ref.get("service")
    if service is None:
        service = IdentityService(
            get_identity_repository(),
            email_sender=build_transactional_email_sender(),
            config=get_identity_config(),
        )
        _identity_service_ref["service"] = service
    return service


def _trusted_proxy_enabled() -> bool:
    """Return whether reverse-proxy client IP headers are trusted."""
    value = get_settings().get("TRUSTED_PROXY")
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in TRUTHY_VALUES


def get_client_ip(request: Request) -> str | None:
    """Return the client IP for rate limiting.

    ``X-Forwarded-For`` is honored only when ``ORCHEO_TRUSTED_PROXY`` is
    enabled, because clients can otherwise spoof that header directly.
    """
    if _trusted_proxy_enabled():
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            first = forwarded_for.split(",", 1)[0].strip()
            if first:
                return first
    return request.client.host if request.client else None


IdentityServiceDep = Annotated[IdentityService, Depends(get_identity_service)]
