"""PostgreSQL-backed implementation of the identity repository."""

from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, NoReturn, cast
from uuid import UUID
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from orcheo.identity.errors import (
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
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
from orcheo.postgres_pools import get_shared_sync_pool


__all__ = ["PostgresIdentityRepository"]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresIdentityRepository:
    """Persistent identity store backed by PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        statement_timeout_ms: int = 10_000,
        lock_timeout_ms: int = 3_000,
    ) -> None:
        """Open or create a PostgreSQL database for identity storage."""
        if statement_timeout_ms <= 0 or lock_timeout_ms <= 0:
            raise ValueError("Identity PostgreSQL timeouts must be positive.")
        self._statement_timeout_ms = statement_timeout_ms
        self._lock_timeout_ms = lock_timeout_ms
        self._dsn = dsn
        self._pool = get_shared_sync_pool(
            ConnectionPool,
            dsn,
            min_size=pool_min_size,
            max_size=pool_max_size,
            row_factory=dict_row,
        )
        self._ensure_schema()

    @contextmanager
    def _connect(self, *, apply_timeouts: bool = True) -> Iterator[Connection[Any]]:
        # The pool commits on a clean exit and rolls back on an exception.
        with self._pool.connection() as connection:
            if apply_timeouts:
                # SET LOCAL semantics survive transaction pooling and do not
                # leak identity budgets to other users of the shared pool.
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true), "
                    "set_config('lock_timeout', %s, true)",
                    (
                        f"{self._statement_timeout_ms}ms",
                        f"{self._lock_timeout_ms}ms",
                    ),
                )
            yield connection

    def _ensure_schema(self) -> None:
        with self._connect(apply_timeouts=False) as conn:
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
        self,
        challenge: AuthEmailChallenge,
        *,
        consumed_at: datetime,
        max_attempts: int | None = None,
    ) -> AuthEmailChallenge:
        """Atomically consume an unexpired challenge below the attempt limit."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_email_challenges
                   SET consumed_at = %s
                 WHERE id = %s
                   AND consumed_at IS NULL
                   AND expires_at > %s
                   AND (%s::integer IS NULL OR attempts < %s)
                """,
                (
                    consumed_at,
                    str(challenge.id),
                    consumed_at,
                    max_attempts,
                    max_attempts,
                ),
            )
            if cursor.rowcount == 0:
                self._raise_challenge_update_error(
                    conn, challenge.id, now=consumed_at, max_attempts=max_attempts
                )
        return challenge.model_copy(update={"consumed_at": consumed_at})

    def increment_challenge_attempts(
        self, challenge_id: UUID, *, now: datetime, max_attempts: int
    ) -> AuthEmailChallenge:
        """Count a failed guess without losing concurrent increments."""
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE auth_email_challenges
                   SET attempts = attempts + 1
                 WHERE id = %s
                   AND consumed_at IS NULL
                   AND expires_at > %s
                   AND attempts < %s
                RETURNING *
                """,
                (str(challenge_id), now, max_attempts),
            ).fetchone()
            if row is None:
                self._raise_challenge_update_error(
                    conn, challenge_id, now=now, max_attempts=max_attempts
                )
        return self._row_to_challenge(row)

    def _raise_challenge_update_error(
        self,
        conn: Connection[Any],
        challenge_id: UUID,
        *,
        now: datetime,
        max_attempts: int | None,
    ) -> NoReturn:
        """Distinguish active lockout from a missing, expired or consumed challenge."""
        locked = conn.execute(
            """
            SELECT id FROM auth_email_challenges
             WHERE id = %s
               AND consumed_at IS NULL
               AND expires_at > %s
               AND attempts >= %s
            """,
            (str(challenge_id), now, max_attempts),
        ).fetchone()
        if locked is not None:
            raise IdentityChallengeLockedError("Too many attempts; request a new code.")
        raise IdentityChallengeNotFoundError(str(challenge_id))

    # -- sessions ------------------------------------------------------------

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    id, user_id, refresh_token_hash, created_at,
                    expires_at, revoked_at, user_agent, ip,
                    oauth_client_id, scopes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    session.oauth_client_id,
                    None if session.scopes is None else Jsonb(session.scopes),
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
        """Persist rotation/revocation changes; revocation is permanent."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                   SET refresh_token_hash = %s,
                       expires_at = %s,
                       revoked_at = COALESCE(revoked_at, %s),
                       scopes = %s
                 WHERE id = %s
                """,
                (
                    session.refresh_token_hash,
                    session.expires_at,
                    session.revoked_at,
                    None if session.scopes is None else Jsonb(session.scopes),
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

    def rotate_session(
        self, session: AuthSession, *, previous_refresh_hash: str, now: datetime
    ) -> AuthSession:
        """Rotate only the matching active hash, including across processes."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                   SET refresh_token_hash = %s,
                       expires_at = %s,
                       scopes = %s
                 WHERE id = %s
                   AND refresh_token_hash = %s
                   AND revoked_at IS NULL
                   AND expires_at > %s
                """,
                (
                    session.refresh_token_hash,
                    session.expires_at,
                    None if session.scopes is None else Jsonb(session.scopes),
                    str(session.id),
                    previous_refresh_hash,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                raise IdentitySessionNotFoundError(str(session.id))
        return session

    # -- oauth clients & authorization requests ------------------------------

    def add_oauth_client(self, client: OAuthClient) -> OAuthClient:
        """Persist a dynamically registered OAuth client."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_clients (client_id, metadata, created_at)
                VALUES (%s, %s, %s)
                """,
                (client.client_id, Jsonb(client.metadata), client.created_at),
            )
        return client

    def get_oauth_client(self, client_id: str) -> OAuthClient:
        """Return the OAuth client identified by `client_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_clients WHERE client_id = %s",
                (client_id,),
            ).fetchone()
        if row is None:
            raise OAuthClientNotFoundError(client_id)
        return OAuthClient(
            client_id=str(row["client_id"]),
            metadata=dict(cast(dict[str, Any], row["metadata"])),
            created_at=cast(datetime, row["created_at"]),
        )

    def add_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist a pending OAuth authorization request."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_authorization_requests (
                    id, client_id, redirect_uri, redirect_uri_provided_explicitly,
                    code_challenge, state, scopes, resource, created_at,
                    expires_at, user_id, decided_at, code_hash, code_expires_at,
                    consumed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.id,
                    request.client_id,
                    request.redirect_uri,
                    request.redirect_uri_provided_explicitly,
                    request.code_challenge,
                    request.state,
                    Jsonb(request.scopes),
                    request.resource,
                    request.created_at,
                    request.expires_at,
                    None if request.user_id is None else str(request.user_id),
                    request.decided_at,
                    request.code_hash,
                    request.code_expires_at,
                    request.consumed_at,
                ),
            )
        return request

    def get_authorization_request(self, request_id: str) -> OAuthAuthorizationRequest:
        """Return the authorization request identified by `request_id`."""
        return self._fetch_authorization_request("id", request_id)

    def get_authorization_request_by_code_hash(
        self, code_hash: str
    ) -> OAuthAuthorizationRequest:
        """Return the authorization request that issued a code hash."""
        return self._fetch_authorization_request("code_hash", code_hash)

    def _fetch_authorization_request(
        self, column: str, value: str
    ) -> OAuthAuthorizationRequest:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM oauth_authorization_requests WHERE {column} = %s",  # noqa: S608 - fixed column names
                (value,),
            ).fetchone()
        if row is None:
            raise OAuthAuthorizationRequestNotFoundError(value)
        return self._row_to_authorization_request(row)

    def update_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist the user's decision on a still-undecided authorization request."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE oauth_authorization_requests
                   SET user_id = %s,
                       decided_at = %s,
                       code_hash = %s,
                       code_expires_at = %s
                 WHERE id = %s
                   AND decided_at IS NULL
                """,
                (
                    None if request.user_id is None else str(request.user_id),
                    request.decided_at,
                    request.code_hash,
                    request.code_expires_at,
                    request.id,
                ),
            )
            if cursor.rowcount == 0:
                raise OAuthAuthorizationRequestNotFoundError(request.id)
        return request

    def consume_authorization_code(
        self, request_id: str, *, consumed_at: datetime
    ) -> OAuthAuthorizationRequest:
        """Atomically mark an unconsumed authorization code as consumed."""
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE oauth_authorization_requests
                   SET consumed_at = %s
                 WHERE id = %s
                   AND consumed_at IS NULL
                RETURNING *
                """,
                (consumed_at, request_id),
            ).fetchone()
        if row is None:
            raise OAuthAuthorizationRequestNotFoundError(request_id)
        return self._row_to_authorization_request(row)

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
            token_hash=(
                None if row.get("token_hash") is None else str(row["token_hash"])
            ),
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
            oauth_client_id=(
                None
                if row.get("oauth_client_id") is None
                else str(row["oauth_client_id"])
            ),
            scopes=(
                None
                if row.get("scopes") is None
                else [str(item) for item in cast(list[Any], row["scopes"])]
            ),
        )

    @staticmethod
    def _row_to_authorization_request(
        row: dict[str, object],
    ) -> OAuthAuthorizationRequest:
        def _optional_time(key: str) -> datetime | None:
            value = row.get(key)
            return cast(datetime, value) if value else None

        user_id = row.get("user_id")
        return OAuthAuthorizationRequest(
            id=str(row["id"]),
            client_id=str(row["client_id"]),
            redirect_uri=str(row["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(
                row["redirect_uri_provided_explicitly"]
            ),
            code_challenge=str(row["code_challenge"]),
            state=None if row.get("state") is None else str(row["state"]),
            scopes=[str(item) for item in cast(list[Any], row["scopes"])],
            resource=None if row.get("resource") is None else str(row["resource"]),
            created_at=cast(datetime, row["created_at"]),
            expires_at=cast(datetime, row["expires_at"]),
            user_id=None if user_id is None else UUID(str(user_id)),
            decided_at=_optional_time("decided_at"),
            code_hash=None if row.get("code_hash") is None else str(row["code_hash"]),
            code_expires_at=_optional_time("code_expires_at"),
            consumed_at=_optional_time("consumed_at"),
        )
