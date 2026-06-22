"""Identity models for the first-party passwordless email IdP.

These mirror the workspace models in shape and conventions. A ``User`` is the
stable internal identity keyed by a verified, normalized email; workspace
memberships are re-keyed onto ``User.id`` by the cutover backfill. An
``AuthEmailChallenge`` is a single-use magic-link/OTP pending record, and an
``AuthSession`` is a rotating refresh-token record backing a logged-in session.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4
from pydantic import Field, field_validator
from orcheo.models.base import OrcheoBaseModel, _utcnow
from orcheo.workspace.models import normalize_email


__all__ = [
    "AuthEmailChallenge",
    "AuthSession",
    "ChallengePurpose",
    "User",
    "UserStatus",
    "normalize_email",
]


class UserStatus(str, Enum):
    """Lifecycle states for a first-party user."""

    ACTIVE = "active"
    DISABLED = "disabled"


class ChallengePurpose(str, Enum):
    """Purpose of an email challenge.

    A single passwordless entry point serves both sign up and log in, so there
    is one purpose today. The column is kept for forward compatibility (e.g. a
    future email-change challenge).
    """

    LOGIN_OR_SIGNUP = "login_or_signup"


class User(OrcheoBaseModel):
    """Internal identity keyed by a unique, normalized verified email."""

    id: UUID = Field(default_factory=uuid4)
    email: str
    email_verified: bool = False
    name: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = Field(default_factory=_utcnow)
    last_login_at: datetime | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _coerce_email(cls, value: object) -> str:
        return normalize_email(str(value))


class AuthEmailChallenge(OrcheoBaseModel):
    """Single-use, short-TTL magic-link + OTP pending record.

    The raw magic-link token and OTP code are never stored; only their hashes
    are persisted. Both the link and the code verify the same record.
    """

    id: UUID = Field(default_factory=uuid4)
    email: str
    token_hash: str
    code_hash: str
    purpose: ChallengePurpose = ChallengePurpose.LOGIN_OR_SIGNUP
    attempts: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    consumed_at: datetime | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _coerce_email(cls, value: object) -> str:
        return normalize_email(str(value))

    def is_expired(self, *, now: datetime) -> bool:
        """Return True when the challenge has passed its TTL."""
        return now >= self.expires_at

    def is_consumed(self) -> bool:
        """Return True once the challenge has been redeemed."""
        return self.consumed_at is not None


class AuthSession(OrcheoBaseModel):
    """Rotating refresh-token record backing a logged-in session."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    refresh_token_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None

    def is_expired(self, *, now: datetime) -> bool:
        """Return True when the session has passed its TTL."""
        return now >= self.expires_at

    def is_active(self, *, now: datetime) -> bool:
        """Return True when the session is neither revoked nor expired."""
        return self.revoked_at is None and not self.is_expired(now=now)
