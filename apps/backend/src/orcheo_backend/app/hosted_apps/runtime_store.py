"""Process wiring for the app-scoped runtime acceptance service."""

from typing import Any
from orcheo.hosted_apps import (
    AppRuntimeService,
    PostgresAppRuntimeService,
    PostgresHostedAppsRepository,
)


RuntimeService = AppRuntimeService | PostgresAppRuntimeService
_runtime_ref: dict[str, RuntimeService | None] = {"service": None}


def get_app_runtime_service(repository: Any | None = None) -> RuntimeService:
    """Return the current runtime service adapter."""
    service = _runtime_ref["service"]
    if service is None:
        if isinstance(repository, PostgresHostedAppsRepository):
            service = PostgresAppRuntimeService(repository.dsn)
        else:
            service = AppRuntimeService()
        _runtime_ref["service"] = service
    return service


def reset_app_runtime_service() -> None:
    """Reset process-local runtime state for isolated tests."""
    service = _runtime_ref["service"]
    if isinstance(service, PostgresAppRuntimeService):
        service.close()
    _runtime_ref["service"] = None
