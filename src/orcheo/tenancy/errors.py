"""Errors raised by the tenancy subsystem."""

from __future__ import annotations


__all__ = [
    "TenantError",
    "TenantNotFoundError",
    "TenantSlugConflictError",
    "TenantMembershipError",
    "TenantPermissionError",
]


class TenantError(Exception):
    """Base class for tenancy failures."""


class TenantNotFoundError(TenantError):
    """Raised when a tenant cannot be located."""


class TenantSlugConflictError(TenantError):
    """Raised when a tenant slug already exists."""


class TenantMembershipError(TenantError):
    """Raised when a membership is missing or invalid."""


class TenantPermissionError(TenantError):
    """Raised when the actor lacks the required role."""
