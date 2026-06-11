"""Repository specific error types."""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base class for repository specific errors."""


class WorkflowNotFoundError(RepositoryError):
    """Raised when a workflow cannot be located."""


class WorkflowVersionNotFoundError(RepositoryError):
    """Raised when attempting to access an unknown workflow version."""


class WorkflowRunNotFoundError(RepositoryError):
    """Raised when attempting to access an unknown workflow run."""


class WorkflowPublishStateError(RepositoryError):
    """Raised when publish state transitions are invalid."""


class WorkflowHandleConflictError(RepositoryError):
    """Raised when a workflow handle conflicts with an existing workflow."""


class CronTriggerNotFoundError(RepositoryError):
    """Raised when a cron trigger config cannot be located."""


class TeamNotFoundError(RepositoryError):
    """Raised when a team cannot be located within a workspace."""


class TeamSlugConflictError(RepositoryError):
    """Raised when a team slug conflicts with an existing team."""


class TeamNotEmptyError(RepositoryError):
    """Raised when attempting to delete a team that still has colleagues."""


__all__ = [
    "RepositoryError",
    "WorkflowNotFoundError",
    "WorkflowVersionNotFoundError",
    "WorkflowRunNotFoundError",
    "WorkflowPublishStateError",
    "WorkflowHandleConflictError",
    "CronTriggerNotFoundError",
    "TeamNotFoundError",
    "TeamSlugConflictError",
    "TeamNotEmptyError",
]
