"""Process-local Hosted Apps repository wiring pending Postgres configuration."""

from __future__ import annotations
import os
from orcheo.hosted_apps import InMemoryHostedAppsRepository
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError


_repository_ref: dict[str, InMemoryHostedAppsRepository | None] = {"repository": None}


def _auto_enable_ephemeral_runtime(repository: InMemoryHostedAppsRepository) -> None:
    """Enable ephemeral local/single-node runtime when startup requests it."""
    enabled = os.getenv("ORCHEO_HOSTED_APPS_AUTO_ENABLE_RUNTIME", "false")
    if enabled.strip().lower() not in {"1", "true", "yes"}:
        return
    try:
        settings = HostedAppsSettings.from_environment()
    except HostedAppsSettingsError:
        return
    if settings.enabled and settings.deployment_mode in {"local", "single-node"}:
        repository.set_runtime_enabled(enabled=True, actor="system:stack-startup")


def get_hosted_apps_repository() -> InMemoryHostedAppsRepository:
    """Return the Hosted Apps repository used by the control-plane routes."""
    repository = _repository_ref["repository"]
    if repository is None:
        repository = InMemoryHostedAppsRepository()
        _auto_enable_ephemeral_runtime(repository)
        _repository_ref["repository"] = repository
    return repository


def set_hosted_apps_repository(repository: InMemoryHostedAppsRepository | None) -> None:
    """Override the repository for tests and controlled embedded deployments."""
    _repository_ref["repository"] = repository


def reset_hosted_apps_repository() -> None:
    """Discard the process-local repository between isolated test runs."""
    set_hosted_apps_repository(None)
