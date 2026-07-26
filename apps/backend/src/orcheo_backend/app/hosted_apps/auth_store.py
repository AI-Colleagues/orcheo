"""Process wiring for Hosted Apps authorization codes and sessions."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from orcheo.hosted_apps import (
    AppAuthService,
    PostgresAppAuthService,
    PostgresHostedAppsRepository,
)


AuthService = AppAuthService | PostgresAppAuthService


@dataclass(slots=True)
class _AuthServiceRef:
    """Mutable process reference paired with its repository dependency."""

    repository: Any | None = None
    service: AuthService | None = None


_auth_ref = _AuthServiceRef()


def _close_auth_service(service: AuthService | None) -> None:
    """Close a durable adapter before replacing or resetting it."""
    if isinstance(service, PostgresAppAuthService):
        service.close()


def get_app_auth_service(repository: Any | None = None) -> AuthService:
    """Return the durable production or in-memory test auth adapter."""
    service = _auth_ref.service
    if service is None or (
        repository is not None and repository is not _auth_ref.repository
    ):
        _close_auth_service(service)
        if isinstance(repository, PostgresHostedAppsRepository):
            service = PostgresAppAuthService(repository.dsn)
        else:
            service = AppAuthService()
        _auth_ref.repository = repository
        _auth_ref.service = service
    return service


def reset_app_auth_service() -> None:
    """Reset auth state for isolated tests and close durable connections."""
    _close_auth_service(_auth_ref.service)
    _auth_ref.repository = None
    _auth_ref.service = None
