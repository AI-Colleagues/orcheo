"""Error hierarchy for the first-party identity subsystem."""

from __future__ import annotations


__all__ = [
    "IdentityChallengeError",
    "IdentityChallengeExpiredError",
    "IdentityChallengeLockedError",
    "IdentityChallengeNotFoundError",
    "IdentityError",
    "IdentitySessionError",
    "IdentitySessionNotFoundError",
    "UserNotFoundError",
]


class IdentityError(Exception):
    """Base class for all identity-subsystem errors."""


class UserNotFoundError(IdentityError):
    """Raised when a user cannot be located by id or email."""

    def __init__(self, identifier: str) -> None:
        """Record the missing user identifier."""
        super().__init__(f"No user found for {identifier}")
        self.identifier = identifier


class IdentityChallengeError(IdentityError):
    """Base class for email-challenge errors."""


class IdentityChallengeNotFoundError(IdentityChallengeError):
    """Raised when an email challenge cannot be located."""


class IdentityChallengeExpiredError(IdentityChallengeError):
    """Raised when a challenge is expired or already consumed."""


class IdentityChallengeLockedError(IdentityChallengeError):
    """Raised when a challenge has exceeded its allowed attempts."""


class IdentitySessionError(IdentityError):
    """Base class for session/refresh-token errors."""


class IdentitySessionNotFoundError(IdentitySessionError):
    """Raised when a session cannot be located or is revoked/expired."""
