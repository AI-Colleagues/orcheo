"""Process wiring for the app-scoped runtime acceptance service."""

from dataclasses import dataclass
from typing import Any
from orcheo.hosted_apps import (
    AppRuntimeService,
    PostgresAppRuntimeService,
    PostgresHostedAppsRepository,
)


RuntimeService = AppRuntimeService | PostgresAppRuntimeService


@dataclass(slots=True)
class _RuntimeServiceRef:
    """Mutable process reference paired with its repository dependency."""

    repository: Any | None = None
    service: RuntimeService | None = None


_runtime_ref = _RuntimeServiceRef()


def _close_runtime_service(service: RuntimeService | None) -> None:
    """Close a durable adapter before replacing or resetting it."""
    if isinstance(service, PostgresAppRuntimeService):
        service.close()


def get_app_runtime_service(repository: Any | None = None) -> RuntimeService:
    """Return the current runtime service adapter."""
    service = _runtime_ref.service
    if service is None or (
        repository is not None and repository is not _runtime_ref.repository
    ):
        _close_runtime_service(service)
        if isinstance(repository, PostgresHostedAppsRepository):
            service = PostgresAppRuntimeService(repository.dsn)
        else:
            service = AppRuntimeService()
        _runtime_ref.repository = repository
        _runtime_ref.service = service
    return service


def reset_app_runtime_service() -> None:
    """Reset process-local runtime state for isolated tests."""
    _close_runtime_service(_runtime_ref.service)
    _runtime_ref.repository = None
    _runtime_ref.service = None
