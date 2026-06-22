"""Tests for the PostgreSQL-backed identity repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from orcheo.identity import (
    AuthEmailChallenge,
    AuthSession,
    ChallengePurpose,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    PostgresIdentityRepository,
    User,
    UserNotFoundError,
)
from orcheo.identity import postgres_store as pg_store


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake connection that records queries and returns canned responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        statement = query.strip()
        self.queries.append((statement, params))
        if statement.startswith("CREATE") or statement.startswith("ALTER"):
            return FakeCursor()
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeConnection, str]:
    """Patch psycopg connect and return the fake connection plus DSN."""
    connection = FakeConnection([])
    monkeypatch.setattr(pg_store, "connect", lambda dsn, row_factory=None: connection)
    return connection, "postgresql://test"


def _user_row(user: User, *, last_login_at: datetime | None) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.email_verified,
        "name": user.name,
        "status": user.status.value,
        "created_at": user.created_at,
        "last_login_at": last_login_at,
    }


def _challenge_row(
    challenge: AuthEmailChallenge, *, consumed_at: datetime | None
) -> dict[str, Any]:
    return {
        "id": challenge.id,
        "email": challenge.email,
        "token_hash": challenge.token_hash,
        "code_hash": challenge.code_hash,
        "purpose": challenge.purpose.value,
        "attempts": challenge.attempts,
        "created_at": challenge.created_at,
        "expires_at": challenge.expires_at,
        "consumed_at": consumed_at,
    }


def _session_row(
    session: AuthSession,
    *,
    revoked_at: datetime | None,
    user_agent: str | None,
    ip: str | None,
) -> dict[str, Any]:
    return {
        "id": session.id,
        "user_id": session.user_id,
        "refresh_token_hash": session.refresh_token_hash,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "revoked_at": revoked_at,
        "user_agent": user_agent,
        "ip": ip,
    }


def test_postgres_identity_repository_roundtrip(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    """Exercise the happy-path persistence and row-mapper branches."""
    connection, dsn = fake_connect
    repo = PostgresIdentityRepository(dsn)

    user = User(
        email="Alice@example.com",
        name="Alice",
        last_login_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    challenge = AuthEmailChallenge(
        email=user.email,
        token_hash="token-hash",
        code_hash="code-hash",
        purpose=ChallengePurpose.LOGIN_OR_SIGNUP,
        attempts=2,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
        consumed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash="refresh-hash",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 2, 1, tzinfo=UTC),
        revoked_at=datetime(2026, 1, 15, tzinfo=UTC),
        user_agent="pytest",
        ip="127.0.0.1",
    )

    connection._responses.extend(
        [
            {},  # create_user
            {"row": _user_row(user, last_login_at=user.last_login_at)},
            {"row": _user_row(user, last_login_at=user.last_login_at)},
            {"rowcount": 1},  # update_user
            {},  # add_challenge
            {"row": _challenge_row(challenge, consumed_at=challenge.consumed_at)},
            {"row": _challenge_row(challenge, consumed_at=challenge.consumed_at)},
            {"row": _challenge_row(challenge, consumed_at=challenge.consumed_at)},
            {"rowcount": 1},  # update_challenge
            {"rowcount": 1},  # consume_challenge
            {},  # add_session
            {
                "row": _session_row(
                    session,
                    revoked_at=session.revoked_at,
                    user_agent=session.user_agent,
                    ip=session.ip,
                )
            },
            {
                "row": _session_row(
                    session,
                    revoked_at=session.revoked_at,
                    user_agent=session.user_agent,
                    ip=session.ip,
                )
            },
            {"rowcount": 1},  # update_session
            {"rowcount": 2},  # revoke_sessions_for_user
        ]
    )

    assert repo.create_user(user) == user
    assert repo.get_user(user.id) == user
    assert repo.get_user_by_email("ALICE@example.com") == user
    assert repo.update_user(user) == user

    assert repo.add_challenge(challenge) == challenge
    assert repo.get_challenge(challenge.id) == challenge
    assert repo.get_challenge_by_token_hash("token-hash") == challenge
    assert (
        repo.find_active_challenge_for_email(
            user.email, now=datetime(2026, 1, 1, tzinfo=UTC)
        )
        == challenge
    )
    assert repo.update_challenge(challenge) == challenge
    assert repo.consume_challenge(
        challenge, consumed_at=datetime(2026, 1, 3, tzinfo=UTC)
    ).consumed_at == datetime(2026, 1, 3, tzinfo=UTC)

    assert repo.add_session(session) == session
    assert repo.get_session(session.id) == session
    assert repo.get_session_by_refresh_hash("refresh-hash") == session
    assert repo.update_session(session) == session
    assert repo.revoke_sessions_for_user(user.id) == 2
    assert connection.commits >= 1
    assert connection.closed >= 1


def test_postgres_identity_repository_duplicate_and_missing_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    """Exercise duplicate-user rollback and not-found/error branches."""
    connection, dsn = fake_connect
    repo = PostgresIdentityRepository(dsn)

    user = User(email="bob@example.com")
    missing_challenge = AuthEmailChallenge(
        email="bob@example.com",
        token_hash="missing-token",
        code_hash="missing-code",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    missing_session = AuthSession(
        user_id=uuid4(),
        refresh_token_hash="missing-refresh",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    connection._responses.extend(
        [
            UniqueViolation("duplicate"),
        ]
    )
    with pytest.raises(ValueError, match="already exists"):
        repo.create_user(user)
    assert connection.rollbacks >= 1

    connection._responses.extend(
        [
            None,  # get_user_by_email -> None
            None,  # find_active_challenge_for_email -> None
            None,  # get_user -> not found
            {"rowcount": 0},  # update_user -> not found
            None,  # get_challenge -> not found
            None,  # get_challenge_by_token_hash -> not found
            {"rowcount": 0},  # update_challenge -> not found
            {"rowcount": 0},  # consume_challenge -> not found
            None,  # get_session -> not found
            None,  # get_session_by_refresh_hash -> not found
            {"rowcount": 0},  # update_session -> not found
        ]
    )

    assert repo.get_user_by_email("missing@example.com") is None
    assert (
        repo.find_active_challenge_for_email(
            "missing@example.com", now=datetime(2026, 1, 1, tzinfo=UTC)
        )
        is None
    )
    with pytest.raises(UserNotFoundError):
        repo.get_user(uuid4())
    with pytest.raises(UserNotFoundError):
        repo.update_user(user)
    with pytest.raises(IdentityChallengeNotFoundError):
        repo.get_challenge(uuid4())
    with pytest.raises(IdentityChallengeNotFoundError):
        repo.get_challenge_by_token_hash("missing-token")
    with pytest.raises(IdentityChallengeNotFoundError):
        repo.update_challenge(missing_challenge)
    with pytest.raises(IdentityChallengeNotFoundError):
        repo.consume_challenge(
            missing_challenge, consumed_at=datetime(2026, 1, 3, tzinfo=UTC)
        )
    with pytest.raises(IdentitySessionNotFoundError):
        repo.get_session(uuid4())
    with pytest.raises(IdentitySessionNotFoundError):
        repo.get_session_by_refresh_hash("missing-refresh")
    with pytest.raises(IdentitySessionNotFoundError):
        repo.update_session(missing_session)


def test_postgres_identity_repository_row_mappers_handle_nulls() -> None:
    """Static mappers should accept nullable DB columns."""
    user = User(email="carol@example.com")
    challenge = AuthEmailChallenge(
        email="carol@example.com",
        token_hash="token",
        code_hash="code",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    session = AuthSession(
        user_id=uuid4(),
        refresh_token_hash="refresh",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    mapped_user = PostgresIdentityRepository._row_to_user(
        _user_row(user, last_login_at=None)
    )
    mapped_challenge = PostgresIdentityRepository._row_to_challenge(
        _challenge_row(challenge, consumed_at=None)
    )
    mapped_session = PostgresIdentityRepository._row_to_session(
        _session_row(session, revoked_at=None, user_agent=None, ip=None)
    )

    assert mapped_user.name is None
    assert mapped_user.last_login_at is None
    assert mapped_challenge.consumed_at is None
    assert mapped_session.revoked_at is None
    assert mapped_session.user_agent is None
    assert mapped_session.ip is None
