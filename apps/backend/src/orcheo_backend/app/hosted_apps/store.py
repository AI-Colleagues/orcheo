"""Hosted Apps repository wiring."""

from __future__ import annotations
import os
from orcheo.hosted_apps import (
    HostedAppsRepository,
    PostgresHostedAppsRepository,
)
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError


_repository_ref: dict[str, HostedAppsRepository | None] = {"repository": None}


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


def set_hosted_apps_repository(repository: HostedAppsRepository | None) -> None:
    """Override the repository for tests and controlled embedded deployments."""
    current = _repository_ref["repository"]
    if isinstance(current, PostgresHostedAppsRepository) and current is not repository:
        current.close()
    _repository_ref["repository"] = repository


def reset_hosted_apps_repository() -> None:
    """Discard the process-local repository between isolated test runs."""
    set_hosted_apps_repository(None)
