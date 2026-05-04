"""HTTP error helpers for tenancy enforcement."""

from __future__ import annotations
from typing import Any, NoReturn
from fastapi import HTTPException, status


__all__ = [
    "TenantContextRequiredError",
    "TenantHTTPError",
    "raise_tenant_forbidden",
    "raise_tenant_not_found",
    "raise_tenant_required",
]


class TenantHTTPError(HTTPException):
    """HTTPException subclass that carries an `error_code` for clients."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Build a structured tenant HTTP error payload."""
        payload: dict[str, Any] = {
            "error": {"code": error_code, "message": message},
        }
        if details:
            payload["error"]["details"] = dict(details)
        super().__init__(status_code=status_code, detail=payload)
        self.error_code = error_code
        self.message = message


class TenantContextRequiredError(TenantHTTPError):
    """Raised when an authenticated request lacks tenant context."""

    def __init__(self, message: str = "Tenant context is required") -> None:
        """Initialize with the default tenant-required error code."""
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=message,
            error_code="tenant.required",
        )


def raise_tenant_not_found(message: str = "Tenant not found") -> NoReturn:
    """Raise a 404 Tenant-not-found error."""
    raise TenantHTTPError(
        status_code=status.HTTP_404_NOT_FOUND,
        message=message,
        error_code="tenant.not_found",
    )


def raise_tenant_forbidden(
    message: str = "Forbidden", *, error_code: str = "tenant.forbidden"
) -> NoReturn:
    """Raise a 403 Forbidden error scoped to tenant access."""
    raise TenantHTTPError(
        status_code=status.HTTP_403_FORBIDDEN,
        message=message,
        error_code=error_code,
    )


def raise_tenant_required(message: str = "Tenant context is required") -> NoReturn:
    """Raise a 400 error when tenant resolution is required but absent."""
    raise TenantContextRequiredError(message)
