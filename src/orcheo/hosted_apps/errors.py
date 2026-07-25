"""Stable domain errors for Hosted Apps."""

from __future__ import annotations


class HostedAppError(ValueError):
    """Base error for Hosted Apps domain operations."""

    code = "hosted_apps.error"


class AliasValidationError(HostedAppError):
    """Raised when an alias is not a valid DNS label for an app."""

    code = "hosted_apps.alias_invalid"


class ReservedAliasError(AliasValidationError):
    """Raised when an alias belongs to the platform-reserved namespace."""

    code = "hosted_apps.alias_reserved"


class AliasConflictError(HostedAppError):
    """Raised when an alias is currently owned by another app."""

    code = "hosted_apps.alias_conflict"


class AliasTombstonedError(HostedAppError):
    """Raised when an alias cannot yet be reused after release."""

    code = "hosted_apps.alias_tombstoned"


class HostedAppsDisabledError(HostedAppError):
    """Raised when the feature or its durable runtime has been disabled."""

    code = "hosted_apps.disabled"


class BundleValidationError(HostedAppError):
    """Raised when an uploaded bundle violates a safe static-bundle policy."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize the stable failure code and author-safe message."""
        super().__init__(message)
        self.code = code
