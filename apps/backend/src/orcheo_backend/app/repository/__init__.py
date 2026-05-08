"""Repository package exports."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any
from orcheo.models.workflow import (
    Workflow,
    WorkflowRun,
    WorkflowRunRemediation,
    WorkflowVersion,
)
from orcheo_backend.app.repository.errors import (
    CronTriggerNotFoundError,
    RepositoryError,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowPublishStateError,
    WorkflowRunNotFoundError,
    WorkflowRunRemediationNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.in_memory import InMemoryWorkflowRepository
from orcheo_backend.app.repository.protocol import VersionDiff, WorkflowRepository


if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from orcheo_backend.app.repository_sqlite import SqliteWorkflowRepository


def __getattr__(name: str) -> Any:
    """Provide lazy access to optional repository implementations."""
    if name == "SqliteWorkflowRepository":
        from orcheo_backend.app.repository_sqlite import (
            SqliteWorkflowRepository as _SqliteWorkflowRepository,
        )

        return _SqliteWorkflowRepository
    raise AttributeError(name)


__all__ = [
    "WorkflowRepository",
    "InMemoryWorkflowRepository",
    "SqliteWorkflowRepository",
    "CronTriggerNotFoundError",
    "RepositoryError",
    "VersionDiff",
    "WorkflowHandleConflictError",
    "Workflow",
    "WorkflowNotFoundError",
    "WorkflowPublishStateError",
    "WorkflowRun",
    "WorkflowRunNotFoundError",
    "WorkflowRunRemediation",
    "WorkflowRunRemediationNotFoundError",
    "WorkflowVersion",
    "WorkflowVersionNotFoundError",
]
