"""Process wiring for the app-scoped runtime acceptance service."""

from orcheo.hosted_apps import AppRuntimeService


_runtime_ref: dict[str, AppRuntimeService | None] = {"service": None}


def get_app_runtime_service() -> AppRuntimeService:
    """Return the current runtime service adapter."""
    service = _runtime_ref["service"]
    if service is None:
        service = AppRuntimeService()
        _runtime_ref["service"] = service
    return service


def reset_app_runtime_service() -> None:
    """Reset process-local runtime state for isolated tests."""
    _runtime_ref["service"] = None
