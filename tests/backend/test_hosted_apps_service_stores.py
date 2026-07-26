"""Process wiring tests for repository-scoped Hosted Apps services."""

from orcheo_backend.app.hosted_apps.auth_store import (
    get_app_auth_service,
    reset_app_auth_service,
)
from orcheo_backend.app.hosted_apps.runtime_store import (
    get_app_runtime_service,
    reset_app_runtime_service,
)


def test_auth_service_tracks_the_repository_instance() -> None:
    """A dependency override cannot inherit another repository's auth state."""
    first_repository = object()
    second_repository = object()
    try:
        first = get_app_auth_service(first_repository)
        assert get_app_auth_service(first_repository) is first

        second = get_app_auth_service(second_repository)
        assert second is not first
        assert get_app_auth_service() is second
    finally:
        reset_app_auth_service()


def test_runtime_service_tracks_the_repository_instance() -> None:
    """A dependency override cannot inherit another repository's runtime state."""
    first_repository = object()
    second_repository = object()
    try:
        first = get_app_runtime_service(first_repository)
        assert get_app_runtime_service(first_repository) is first

        second = get_app_runtime_service(second_repository)
        assert second is not first
        assert get_app_runtime_service() is second
    finally:
        reset_app_runtime_service()
