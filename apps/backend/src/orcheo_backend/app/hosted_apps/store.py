"""Hosted Apps repository wiring."""

from __future__ import annotations
import os
from pathlib import Path
from orcheo.hosted_apps import (
    AppBundleStore,
    FilesystemBundleStore,
    HostedAppsRepository,
    PostgresBundleStore,
    PostgresHostedAppsRepository,
    migrate_filesystem_bundles,
)
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError


_repository_ref: dict[str, HostedAppsRepository | None] = {"repository": None}
_bundle_store_ref: dict[str, AppBundleStore | None] = {"store": None}
_bundle_store_key: dict[str, tuple[str, str, str] | None] = {"key": None}
_bundle_store_override: dict[str, bool] = {"enabled": False}


def _auto_enable_self_hosted_runtime(repository: HostedAppsRepository) -> None:
    """Enable local/single-node delivery once without resetting durable state."""
    enabled = os.getenv("ORCHEO_HOSTED_APPS_AUTO_ENABLE_RUNTIME", "false")
    if enabled.strip().lower() not in {"1", "true", "yes"}:
        return
    try:
        settings = HostedAppsSettings.from_environment()
    except HostedAppsSettingsError:
        return
    runtime = repository.get_runtime_generation()
    if (
        settings.enabled
        and settings.deployment_mode in {"local", "single-node"}
        and not runtime.enabled
    ):
        repository.set_runtime_enabled(enabled=True, actor="system:stack-startup")


def get_hosted_apps_repository() -> HostedAppsRepository:
    """Return the Hosted Apps repository used by the control-plane routes."""
    repository = _repository_ref["repository"]
    if repository is None:
        dsn = os.getenv("ORCHEO_POSTGRES_DSN", "").strip()
        if not dsn:
            msg = "ORCHEO_POSTGRES_DSN must be set for Hosted Apps persistence."
            raise ValueError(msg)
        repository = PostgresHostedAppsRepository(dsn)
        _auto_enable_self_hosted_runtime(repository)
        _repository_ref["repository"] = repository
    return repository


def get_app_bundle_store() -> AppBundleStore:
    """Return the configured durable bundle object store."""
    settings = HostedAppsSettings.from_environment()
    dsn = os.getenv("ORCHEO_POSTGRES_DSN", "").strip()
    filesystem_root = str(settings.filesystem_root or "")
    configuration_key = (settings.bundle_backend or "", dsn, filesystem_root)
    current = _bundle_store_ref["store"]
    if current is not None and _bundle_store_override["enabled"]:
        return current
    if current is not None and _bundle_store_key["key"] == configuration_key:
        return current
    _close_bundle_store(current)
    if settings.bundle_backend == "postgres":
        if not dsn:
            raise ValueError(
                "ORCHEO_POSTGRES_DSN must be set for PostgreSQL bundle storage."
            )
        store: AppBundleStore = PostgresBundleStore(dsn)
        if settings.filesystem_root is not None:
            migrate_filesystem_bundles(settings.filesystem_root, store)
    elif (
        settings.bundle_backend == "filesystem" and settings.filesystem_root is not None
    ):
        store = FilesystemBundleStore(Path(settings.filesystem_root))
    else:
        raise HostedAppsSettingsError(
            "The configured bundle backend requires an external upload adapter."
        )
    _bundle_store_ref["store"] = store
    _bundle_store_key["key"] = configuration_key
    return store


def _close_bundle_store(store: AppBundleStore | None) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        close()


def set_app_bundle_store(store: AppBundleStore | None) -> None:
    """Override the bundle store for tests and embedded deployments."""
    current = _bundle_store_ref["store"]
    if current is not store:
        _close_bundle_store(current)
    _bundle_store_ref["store"] = store
    _bundle_store_key["key"] = None
    _bundle_store_override["enabled"] = store is not None


def reset_app_bundle_store() -> None:
    """Discard the cached bundle store."""
    set_app_bundle_store(None)
    _bundle_store_override["enabled"] = False


def set_hosted_apps_repository(repository: HostedAppsRepository | None) -> None:
    """Override the repository for tests and controlled embedded deployments."""
    current = _repository_ref["repository"]
    if isinstance(current, PostgresHostedAppsRepository) and current is not repository:
        current.close()
    _repository_ref["repository"] = repository


def reset_hosted_apps_repository() -> None:
    """Discard the process-local repository between isolated test runs."""
    set_hosted_apps_repository(None)
    reset_app_bundle_store()
