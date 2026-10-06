"""Identity models for the first-party passwordless email IdP.

These mirror the workspace models in shape and conventions. A ``User`` is the
stable internal identity keyed by a verified, normalized email; workspace
memberships are re-keyed onto ``User.id`` by the cutover backfill. An
``AuthEmailChallenge`` is a single-use emailed sign-in code, and an
``AuthSession`` is a rotating refresh-token record backing a logged-in session.
A ``Passkey`` is a WebAuthn credential a signed-in user added as a second way
to sign in, and a ``PasskeyChallenge`` backs one passkey ceremony.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import Field, field_validator
from orcheo.models.base import OrcheoBaseModel, _utcnow
from orcheo.workspace.models import normalize_email


__all__ = [
    "AuthEmailChallenge",
    "AuthSession",
    "ChallengePurpose",
    "OAuthAuthorizationRequest",
    "OAuthClient",
    "Passkey",
    "PasskeyCeremony",
    "PasskeyChallenge",
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
    """Single-use, short-TTL sign-in code sent by email.

    The raw code is never stored; only its hash is persisted. ``token_hash``
    belonged to the retired magic-link flow and is unset for new challenges.
    """

    id: UUID = Field(default_factory=uuid4)
    email: str
    token_hash: str | None = None
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
    """Rotating refresh-token record backing a logged-in session.

    Studio sessions leave ``oauth_client_id`` unset. Sessions granted to an
    OAuth client (e.g. an MCP client) record the client and the scopes the
    user approved, and can only be refreshed by that client.
    """

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    refresh_token_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None
    oauth_client_id: str | None = None
    scopes: list[str] | None = None

    def is_expired(self, *, now: datetime) -> bool:
        """Return True when the session has passed its TTL."""
        return now >= self.expires_at

    def is_active(self, *, now: datetime) -> bool:
        """Return True when the session is neither revoked nor expired."""
        return self.revoked_at is None and not self.is_expired(now=now)


class OAuthClient(OrcheoBaseModel):
    """An OAuth client registered through dynamic client registration.

    ``metadata`` holds the RFC 7591 registration document as returned to the
    client, minus any client secret: confidential clients' secrets are derived
    from the client ID and never stored.
    """

    client_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class OAuthAuthorizationRequest(OrcheoBaseModel):
    """A pending OAuth authorization awaiting the user's consent.

    The record is created when a client starts the authorization-code flow,
    bound to a user when they approve it in Studio (which issues a single-use
    code, stored only as a hash), and consumed when the code is exchanged.
    """

    id: str
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    state: str | None = None
    scopes: list[str] = Field(default_factory=list)
    resource: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    user_id: UUID | None = None
    decided_at: datetime | None = None
    code_hash: str | None = None
    code_expires_at: datetime | None = None
    consumed_at: datetime | None = None

    def is_pending(self, *, now: datetime) -> bool:
        """Return True while the request still awaits the user's decision."""
        return self.decided_at is None and now < self.expires_at


class PasskeyCeremony(str, Enum):
    """The WebAuthn ceremony a passkey challenge was issued for."""

    REGISTRATION = "registration"
    AUTHENTICATION = "authentication"


class PasskeyChallenge(OrcheoBaseModel):
    """Single-use challenge backing one passkey (WebAuthn) ceremony.

    The challenge is sent to the browser and signed by the authenticator, so it
    is stored as issued (base64url). Registration challenges are bound to the
    signed-in user adding a passkey; sign-in challenges are not, because passkey
    sign-in starts before the account is known.
    """

    id: UUID = Field(default_factory=uuid4)
    ceremony: PasskeyCeremony
    challenge: str
    user_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime

    def is_expired(self, *, now: datetime) -> bool:
        """Return True when the challenge has passed its TTL."""
        return now >= self.expires_at


class Passkey(OrcheoBaseModel):
    """A WebAuthn credential (passkey) registered to a user.

    ``credential_id`` is the authenticator-assigned credential id and
    ``public_key`` the COSE public key that verifies sign-in assertions, both
    base64url-encoded. ``sign_count`` is the last signature counter the
    authenticator reported; synced passkeys always report 0.
    """

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    credential_id: str
    public_key: str
    sign_count: int = 0
    transports: list[str] = Field(default_factory=list)
    aaguid: str | None = None
    backup_eligible: bool = False
    backed_up: bool = False
    name: str
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None
