"""PostgreSQL-backed implementation of the identity repository."""

from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID
from psycopg import Connection, connect
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from orcheo.identity.errors import (
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    UserNotFoundError,
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


__all__ = ["PostgresIdentityRepository"]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresIdentityRepository:
    """Persistent identity store backed by PostgreSQL."""

    def __init__(self, dsn: str) -> None:
        """Open or create a PostgreSQL database for identity storage."""
        self._dsn = dsn
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Connection[Any]]:
        connection = connect(self._dsn, row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            for statement in POSTGRES_IDENTITY_SCHEMA.strip().split(";"):
                sql = statement.strip()
                if sql:
                    conn.execute(sql)

    # -- users ---------------------------------------------------------------

    def create_user(self, user: User) -> User:
        """Persist a new user; raises on a duplicate email."""
        normalized = normalize_email(user.email)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        id, email, email_verified, name, status,
                        created_at, last_login_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(user.id),
                        normalized,
                        user.email_verified,
                        user.name,
                        user.status.value,
                        user.created_at,
                        user.last_login_at,
                    ),
                )
        except UniqueViolation as exc:
            msg = f"A user already exists for {normalized}"
            raise ValueError(msg) from exc
        return user

    def get_user(self, user_id: UUID) -> User:
        """Return the user identified by `user_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = %s",
                (str(user_id),),
            ).fetchone()
        if row is None:
            raise UserNotFoundError(str(user_id))
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> User | None:
        """Return the user with a matching normalized email, or None."""
        normalized = normalize_email(email)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = %s",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    def update_user(self, user: User) -> User:
        """Persist mutable user fields (verification, name, status, login)."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                   SET email = %s,
                       email_verified = %s,
                       name = %s,
                       status = %s,
                       last_login_at = %s
                 WHERE id = %s
                """,
                (
                    normalize_email(user.email),
                    user.email_verified,
                    user.name,
                    user.status.value,
                    user.last_login_at,
                    str(user.id),
                ),
            )
            if cursor.rowcount == 0:
                raise UserNotFoundError(str(user.id))
        return user

    # -- challenges ----------------------------------------------------------

    def add_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist a new email challenge."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_email_challenges (
                    id, email, token_hash, code_hash, purpose,
                    attempts, created_at, expires_at, consumed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(challenge.id),
                    challenge.email,
                    challenge.token_hash,
                    challenge.code_hash,
                    challenge.purpose.value,
                    challenge.attempts,
                    challenge.created_at,
                    challenge.expires_at,
                    challenge.consumed_at,
                ),
            )
        return challenge

    def get_challenge(self, challenge_id: UUID) -> AuthEmailChallenge:
        """Return the challenge identified by `challenge_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_email_challenges WHERE id = %s",
                (str(challenge_id),),
            ).fetchone()
        if row is None:
            raise IdentityChallengeNotFoundError(str(challenge_id))
        return self._row_to_challenge(row)

    def get_challenge_by_token_hash(self, token_hash: str) -> AuthEmailChallenge:
        """Return the challenge matching a magic-link token hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_email_challenges WHERE token_hash = %s",
                (token_hash,),
            ).fetchone()
        if row is None:
            raise IdentityChallengeNotFoundError(token_hash)
        return self._row_to_challenge(row)

    def find_active_challenge_for_email(
        self, email: str, *, now: datetime
    ) -> AuthEmailChallenge | None:
        """Return the newest unconsumed, unexpired challenge for an email."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM auth_email_challenges
                 WHERE email = %s
                   AND consumed_at IS NULL
                   AND expires_at > %s
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (normalize_email(email), now),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_challenge(row)

    def update_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist attempt/consumption changes for an existing challenge."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_email_challenges
                   SET attempts = %s,
                       consumed_at = %s
                 WHERE id = %s
                """,
                (challenge.attempts, challenge.consumed_at, str(challenge.id)),
            )
            if cursor.rowcount == 0:
                raise IdentityChallengeNotFoundError(str(challenge.id))
        return challenge

    def consume_challenge(
        self, challenge: AuthEmailChallenge, *, consumed_at: datetime
    ) -> AuthEmailChallenge:
        """Atomically mark an unconsumed challenge as consumed."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_email_challenges
                   SET consumed_at = %s
                 WHERE id = %s
                   AND consumed_at IS NULL
                """,
                (consumed_at, str(challenge.id)),
            )
            if cursor.rowcount == 0:
                raise IdentityChallengeNotFoundError(str(challenge.id))
        return challenge.model_copy(update={"consumed_at": consumed_at})

    # -- sessions ------------------------------------------------------------

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    id, user_id, refresh_token_hash, created_at,
                    expires_at, revoked_at, user_agent, ip
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(session.id),
                    str(session.user_id),
                    session.refresh_token_hash,
                    session.created_at,
                    session.expires_at,
                    session.revoked_at,
                    session.user_agent,
                    session.ip,
                ),
            )
        return session

    def get_session(self, session_id: UUID) -> AuthSession:
        """Return the session identified by `session_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE id = %s",
                (str(session_id),),
            ).fetchone()
        if row is None:
            raise IdentitySessionNotFoundError(str(session_id))
        return self._row_to_session(row)

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> AuthSession:
        """Return the session matching a refresh-token hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE refresh_token_hash = %s",
                (refresh_token_hash,),
            ).fetchone()
        if row is None:
            raise IdentitySessionNotFoundError(refresh_token_hash)
        return self._row_to_session(row)

    def update_session(self, session: AuthSession) -> AuthSession:
        """Persist rotation/revocation changes for an existing session."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                   SET refresh_token_hash = %s,
                       expires_at = %s,
                       revoked_at = %s
                 WHERE id = %s
                """,
                (
                    session.refresh_token_hash,
                    session.expires_at,
                    session.revoked_at,
                    str(session.id),
                ),
            )
            if cursor.rowcount == 0:
                raise IdentitySessionNotFoundError(str(session.id))
        return session

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        """Revoke every active session for a user; return the count revoked."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                   SET revoked_at = %s
                 WHERE user_id = %s
                   AND revoked_at IS NULL
                """,
                (_utc_now(), str(user_id)),
            )
            return cursor.rowcount

    # -- row mappers ---------------------------------------------------------

    @staticmethod
    def _row_to_user(row: dict[str, object]) -> User:
        last_login = row.get("last_login_at")
        return User(
            id=UUID(str(row["id"])),
            email=str(row["email"]),
            email_verified=bool(row["email_verified"]),
            name=None if row.get("name") is None else str(row["name"]),
            status=UserStatus(str(row["status"])),
            created_at=cast(datetime, row["created_at"]),
            last_login_at=cast(datetime, last_login) if last_login else None,
        )

    @staticmethod
    def _row_to_challenge(row: dict[str, object]) -> AuthEmailChallenge:
        consumed_at = row.get("consumed_at")
        return AuthEmailChallenge(
            id=UUID(str(row["id"])),
            email=str(row["email"]),
            token_hash=str(row["token_hash"]),
            code_hash=str(row["code_hash"]),
            purpose=ChallengePurpose(str(row["purpose"])),
            attempts=int(cast(int, row["attempts"])),
            created_at=cast(datetime, row["created_at"]),
            expires_at=cast(datetime, row["expires_at"]),
            consumed_at=cast(datetime, consumed_at) if consumed_at else None,
        )

    @staticmethod
    def _row_to_session(row: dict[str, object]) -> AuthSession:
        revoked_at = row.get("revoked_at")
        return AuthSession(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            refresh_token_hash=str(row["refresh_token_hash"]),
            created_at=cast(datetime, row["created_at"]),
            expires_at=cast(datetime, row["expires_at"]),
            revoked_at=cast(datetime, revoked_at) if revoked_at else None,
            user_agent=(
                None if row.get("user_agent") is None else str(row["user_agent"])
            ),
            ip=None if row.get("ip") is None else str(row["ip"]),
        )
