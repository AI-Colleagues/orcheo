"""Hosted Apps control-plane services and dependency wiring."""

from orcheo_backend.app.hosted_apps.auth_routes import router as auth_router
from orcheo_backend.app.hosted_apps.auth_store import (
    get_app_auth_service,
    reset_app_auth_service,
)
from orcheo_backend.app.hosted_apps.internal import router as internal_router
from orcheo_backend.app.hosted_apps.runtime_store import (
    get_app_runtime_service,
    reset_app_runtime_service,
)
from orcheo_backend.app.hosted_apps.store import (
    get_hosted_apps_repository,
    reset_hosted_apps_repository,
    set_hosted_apps_repository,
)


__all__ = [
    "get_hosted_apps_repository",
    "get_app_auth_service",
    "get_app_runtime_service",
    "auth_router",
    "internal_router",
    "reset_hosted_apps_repository",
    "reset_app_auth_service",
    "reset_app_runtime_service",
    "set_hosted_apps_repository",
]
