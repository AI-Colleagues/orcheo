"""Errors raised by the workspace subsystem."""

from __future__ import annotations


__all__ = [
    "WorkspaceError",
    "WorkspaceInvitationEmailMismatchError",
    "WorkspaceInvitationError",
    "WorkspaceInvitationExpiredError",
    "WorkspaceInvitationNotFoundError",
    "WorkspaceMembershipLimitError",
    "WorkspaceNotFoundError",
    "WorkspaceSlugConflictError",
    "WorkspaceMembershipError",
    "WorkspacePermissionError",
]


class WorkspaceError(Exception):
    """Base class for workspace failures."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a workspace cannot be located."""


class WorkspaceSlugConflictError(WorkspaceError):
    """Raised when a workspace slug already exists."""


class WorkspaceMembershipError(WorkspaceError):
    """Raised when a membership is missing or invalid."""


class WorkspaceMembershipLimitError(WorkspaceMembershipError):
    """Raised when a user would exceed the workspace membership cap."""


class WorkspacePermissionError(WorkspaceError):
    """Raised when the actor lacks the required role."""


class WorkspaceInvitationError(WorkspaceError):
    """Raised when an invitation is missing, duplicated, or in a bad state."""


class WorkspaceInvitationNotFoundError(WorkspaceInvitationError):
    """Raised when an invitation cannot be located by id or token."""


class WorkspaceInvitationExpiredError(WorkspaceInvitationError):
    """Raised when a pending invitation is redeemed after it expired."""


class WorkspaceInvitationEmailMismatchError(WorkspaceInvitationError):
    """Raised when the redeemer's verified email does not match the invite."""
