"""First-party identity core: users, email challenges, sessions, and storage.

This package owns the passwordless-email identity domain. The FastAPI identity
service (``orcheo_backend.app.identity``) builds on these primitives to issue
and verify challenges and mint the JWT contract the backend already validates.
"""

from orcheo.identity.errors import (
    IdentityChallengeError,
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentityEmailDomainNotAllowedError,
    IdentityError,
    IdentitySessionError,
    IdentitySessionNotFoundError,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClientNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    ChallengePurpose,
    OAuthAuthorizationRequest,
    OAuthClient,
    User,
    UserStatus,
    normalize_email,
)
from orcheo.identity.postgres_schema import POSTGRES_IDENTITY_SCHEMA
from orcheo.identity.postgres_store import PostgresIdentityRepository
from orcheo.identity.repository import (
    IdentityRepository,
    InMemoryIdentityRepository,
)


__all__ = [
    "POSTGRES_IDENTITY_SCHEMA",
    "AuthEmailChallenge",
    "AuthSession",
    "ChallengePurpose",
    "IdentityChallengeError",
    "IdentityChallengeExpiredError",
    "IdentityChallengeLockedError",
    "IdentityChallengeNotFoundError",
    "IdentityEmailDomainNotAllowedError",
    "IdentityError",
    "IdentityRepository",
    "IdentitySessionError",
    "IdentitySessionNotFoundError",
    "InMemoryIdentityRepository",
    "OAuthAuthorizationRequest",
    "OAuthAuthorizationRequestNotFoundError",
    "OAuthClient",
    "OAuthClientNotFoundError",
    "PostgresIdentityRepository",
    "User",
    "UserNotFoundError",
    "UserStatus",
    "normalize_email",
]
