"""HTTP error helpers used across routers."""

from __future__ import annotations
from typing import Any, NoReturn
from fastapi import HTTPException, status
from orcheo.triggers.webhook import WebhookValidationError
from orcheo.vault import WorkflowScopeError


def raise_not_found(detail: str, exc: Exception) -> NoReturn:
    """Raise a standardized 404 HTTP error."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
    ) from exc


def raise_conflict(detail: str, exc: Exception) -> NoReturn:
    """Raise a standardized 409 HTTP error."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    ) from exc


def raise_webhook_error(exc: WebhookValidationError) -> NoReturn:
    """Transform webhook validation errors into HTTP errors."""
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def raise_scope_error(exc: WorkflowScopeError) -> NoReturn:
    """Raise a standardized 403 response for scope violations."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=str(exc),
    ) from exc


class TenantLimitError(RuntimeError):
    """Base error for tenant quota and rate-limit violations."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = status.HTTP_429_TOO_MANY_REQUESTS,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        """Initialize with a human-readable message, machine code, and HTTP metadata."""
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        self.retry_after = retry_after

    def as_http_exception(self) -> HTTPException:
        """Return the error as a structured HTTP exception."""
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": dict(self.details),
            }
        }
        headers = {"Retry-After": str(self.retry_after)} if self.retry_after else None
        return HTTPException(
            status_code=self.status_code,
            detail=payload,
            headers=headers,
        )


class TenantQuotaExceededError(TenantLimitError):
    """Raised when a tenant exceeds a configured quota."""


class TenantRateLimitError(TenantLimitError):
    """Raised when a tenant exceeds a configured rate limit."""


__all__ = [
    "raise_conflict",
    "raise_not_found",
    "raise_scope_error",
    "raise_webhook_error",
    "TenantLimitError",
    "TenantQuotaExceededError",
    "TenantRateLimitError",
]
