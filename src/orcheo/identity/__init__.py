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
    IdentityError,
    IdentitySessionError,
    IdentitySessionNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.migration import (
    CoverageReport,
    MigrationReport,
    backfill_identities,
    report_migration_coverage,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    ChallengePurpose,
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
    "CoverageReport",
    "MigrationReport",
    "backfill_identities",
    "report_migration_coverage",
    "IdentityChallengeError",
    "IdentityChallengeExpiredError",
    "IdentityChallengeLockedError",
    "IdentityChallengeNotFoundError",
    "IdentityError",
    "IdentityRepository",
    "IdentitySessionError",
    "IdentitySessionNotFoundError",
    "InMemoryIdentityRepository",
    "PostgresIdentityRepository",
    "User",
    "UserNotFoundError",
    "UserStatus",
    "normalize_email",
]
