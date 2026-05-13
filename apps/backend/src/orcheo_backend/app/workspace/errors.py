"""HTTP error helpers for workspace enforcement."""

from __future__ import annotations
from typing import Any, NoReturn
from fastapi import HTTPException, status


__all__ = [
    "WorkspaceContextRequiredError",
    "WorkspaceHTTPError",
    "raise_workspace_forbidden",
    "raise_workspace_not_found",
    "raise_workspace_required",
]


class WorkspaceHTTPError(HTTPException):
    """HTTPException subclass that carries an `error_code` for clients."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Build a structured workspace HTTP error payload."""
        payload: dict[str, Any] = {
            "error": {"code": error_code, "message": message},
        }
        if details:
            payload["error"]["details"] = dict(details)
        super().__init__(status_code=status_code, detail=payload)
        self.error_code = error_code
        self.message = message


class WorkspaceContextRequiredError(WorkspaceHTTPError):
    """Raised when an authenticated request lacks workspace context."""

    def __init__(self, message: str = "Workspace context is required") -> None:
        """Initialize with the default workspace-required error code."""
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=message,
            error_code="workspace.required",
        )


def raise_workspace_not_found(message: str = "Workspace not found") -> NoReturn:
    """Raise a 404 Workspace-not-found error."""
    raise WorkspaceHTTPError(
        status_code=status.HTTP_404_NOT_FOUND,
        message=message,
        error_code="workspace.not_found",
    )


def raise_workspace_forbidden(
    message: str = "Forbidden", *, error_code: str = "workspace.forbidden"
) -> NoReturn:
    """Raise a 403 Forbidden error scoped to workspace access."""
    raise WorkspaceHTTPError(
        status_code=status.HTTP_403_FORBIDDEN,
        message=message,
        error_code=error_code,
    )


def raise_workspace_required(
    message: str = "Workspace context is required",
) -> NoReturn:
    """Raise a 400 error when workspace resolution is required but absent."""
    raise WorkspaceContextRequiredError(message)
