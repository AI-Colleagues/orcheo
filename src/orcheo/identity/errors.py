"""Error hierarchy for the first-party identity subsystem."""

from __future__ import annotations


__all__ = [
    "IdentityChallengeError",
    "IdentityChallengeExpiredError",
    "IdentityChallengeLockedError",
    "IdentityChallengeNotFoundError",
    "IdentityEmailDomainNotAllowedError",
    "IdentityError",
    "IdentitySessionError",
    "IdentitySessionNotFoundError",
    "OAuthAuthorizationRequestNotFoundError",
    "OAuthClientNotFoundError",
    "PasskeyAlreadyRegisteredError",
    "PasskeyChallengeNotFoundError",
    "PasskeyError",
    "PasskeyNotFoundError",
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


class IdentityEmailDomainNotAllowedError(IdentityError):
    """Raised when an email's domain is outside the sign-in allowlist."""


class IdentitySessionError(IdentityError):
    """Base class for session/refresh-token errors."""


class IdentitySessionNotFoundError(IdentitySessionError):
    """Raised when a session cannot be located or is revoked/expired."""


class OAuthClientNotFoundError(IdentityError):
    """Raised when a registered OAuth client cannot be located."""


class OAuthAuthorizationRequestNotFoundError(IdentityError):
    """Raised when an OAuth authorization request or code cannot be located."""


class PasskeyError(IdentityError):
    """Base class for passkey storage errors."""


class PasskeyNotFoundError(PasskeyError):
    """Raised when a passkey cannot be located (for its owner)."""


class PasskeyAlreadyRegisteredError(PasskeyError):
    """Raised when registering a credential id that is already registered."""


class PasskeyChallengeNotFoundError(PasskeyError):
    """Raised when a passkey challenge is unknown, expired, or already used."""
