"""Process wiring for Hosted Apps authorization codes and sessions."""

from __future__ import annotations
from typing import Any
from orcheo.hosted_apps import (
    AppAuthService,
    PostgresAppAuthService,
    PostgresHostedAppsRepository,
)


AuthService = AppAuthService | PostgresAppAuthService
_auth_ref: dict[str, AuthService | None] = {"service": None}


def get_app_auth_service(repository: Any | None = None) -> AuthService:
    """Return the durable production or in-memory test auth adapter."""
    service = _auth_ref["service"]
    if service is None:
        if isinstance(repository, PostgresHostedAppsRepository):
            service = PostgresAppAuthService(repository.dsn)
        else:
            service = AppAuthService()
        _auth_ref["service"] = service
    return service


def reset_app_auth_service() -> None:
    """Reset auth state for isolated tests and close durable connections."""
    service = _auth_ref["service"]
    if isinstance(service, PostgresAppAuthService):
        service.close()
    _auth_ref["service"] = None
