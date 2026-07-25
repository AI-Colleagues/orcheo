"""Process-local Hosted Apps repository wiring pending Postgres configuration."""

from __future__ import annotations
from orcheo.hosted_apps import InMemoryHostedAppsRepository


_repository_ref: dict[str, InMemoryHostedAppsRepository | None] = {"repository": None}


def get_hosted_apps_repository() -> InMemoryHostedAppsRepository:
    """Return the Hosted Apps repository used by the control-plane routes."""
    repository = _repository_ref["repository"]
    if repository is None:
        repository = InMemoryHostedAppsRepository()
        _repository_ref["repository"] = repository
    return repository


def set_hosted_apps_repository(repository: InMemoryHostedAppsRepository | None) -> None:
    """Override the repository for tests and controlled embedded deployments."""
    _repository_ref["repository"] = repository


def reset_hosted_apps_repository() -> None:
    """Discard the process-local repository between isolated test runs."""
    set_hosted_apps_repository(None)
