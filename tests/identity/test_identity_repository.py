"""Tests for the in-memory identity repository and domain models."""

from __future__ import annotations
from datetime import timedelta
from uuid import uuid4
import pytest
from orcheo.identity import (
    AuthEmailChallenge,
    AuthSession,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    InMemoryIdentityRepository,
    User,
    UserNotFoundError,
    UserStatus,
)
from orcheo.models.base import _utcnow


def _make_user(email: str = "alice@example.com") -> User:
    return User(email=email, name="Alice")


def _make_challenge(email: str = "alice@example.com") -> AuthEmailChallenge:
    return AuthEmailChallenge(
        email=email,
        token_hash="token-hash",
        code_hash="code-hash",
        expires_at=_utcnow() + timedelta(minutes=15),
    )


def test_user_email_is_normalized() -> None:
    user = User(email="  Alice@Example.COM ")
    assert user.email == "alice@example.com"
    assert user.status is UserStatus.ACTIVE
    assert user.email_verified is False


def test_create_and_get_user_by_id_and_email() -> None:
    repo = InMemoryIdentityRepository()
    user = repo.create_user(_make_user())

    assert repo.get_user(user.id) == user
    assert repo.get_user_by_email("ALICE@example.com") == user
    assert repo.get_user_by_email("missing@example.com") is None


def test_create_user_rejects_duplicate_email() -> None:
    repo = InMemoryIdentityRepository()
    repo.create_user(_make_user())
    with pytest.raises(ValueError, match="already exists"):
        repo.create_user(_make_user(email="Alice@example.com"))


def test_get_missing_user_raises() -> None:
    repo = InMemoryIdentityRepository()
    with pytest.raises(UserNotFoundError):
        repo.get_user(_make_user().id)


def test_update_user_persists_verification_and_login() -> None:
    repo = InMemoryIdentityRepository()
    user = repo.create_user(_make_user())
    now = _utcnow()
    updated = repo.update_user(
        user.model_copy(update={"email_verified": True, "last_login_at": now})
    )
    assert updated.email_verified is True
    assert repo.get_user(user.id).last_login_at == now


def test_challenge_roundtrip_and_active_lookup() -> None:
    repo = InMemoryIdentityRepository()
    challenge = repo.add_challenge(_make_challenge())
    now = _utcnow()

    assert repo.get_challenge(challenge.id) == challenge
    assert repo.get_challenge_by_token_hash("token-hash") == challenge
    assert repo.find_active_challenge_for_email("alice@example.com", now=now) == (
        challenge
    )


def test_consumed_challenge_is_not_active() -> None:
    repo = InMemoryIdentityRepository()
    challenge = repo.add_challenge(_make_challenge())
    repo.update_challenge(challenge.model_copy(update={"consumed_at": _utcnow()}))
    assert (
        repo.find_active_challenge_for_email("alice@example.com", now=_utcnow()) is None
    )


def test_expired_challenge_is_not_active() -> None:
    repo = InMemoryIdentityRepository()
    expired = AuthEmailChallenge(
        email="alice@example.com",
        token_hash="t",
        code_hash="c",
        expires_at=_utcnow() - timedelta(minutes=1),
    )
    repo.add_challenge(expired)
    assert (
        repo.find_active_challenge_for_email("alice@example.com", now=_utcnow()) is None
    )


def test_missing_challenge_lookups_raise() -> None:
    repo = InMemoryIdentityRepository()
    with pytest.raises(IdentityChallengeNotFoundError):
        repo.get_challenge_by_token_hash("nope")


def test_session_roundtrip_and_revoke_for_user() -> None:
    repo = InMemoryIdentityRepository()
    user = repo.create_user(_make_user())
    session = repo.add_session(
        AuthSession(
            user_id=user.id,
            refresh_token_hash="rt-hash",
            expires_at=_utcnow() + timedelta(days=30),
        )
    )

    assert repo.get_session(session.id) == session
    assert repo.get_session_by_refresh_hash("rt-hash") == session

    revoked = repo.revoke_sessions_for_user(user.id)
    assert revoked == 1
    assert repo.get_session(session.id).revoked_at is not None
    # Idempotent: already-revoked sessions are not counted again.
    assert repo.revoke_sessions_for_user(user.id) == 0


def test_missing_session_lookup_raises() -> None:
    repo = InMemoryIdentityRepository()
    with pytest.raises(IdentitySessionNotFoundError):
        repo.get_session_by_refresh_hash("nope")


def test_challenge_and_session_lookup_update_paths() -> None:
    repo = InMemoryIdentityRepository()
    user = repo.create_user(_make_user())
    first_challenge = repo.add_challenge(
        _make_challenge(email="first@example.com").model_copy(
            update={"token_hash": "first-token"}
        )
    )
    second_challenge = repo.add_challenge(
        _make_challenge(email="second@example.com").model_copy(
            update={"token_hash": "second-token"}
        )
    )

    assert repo.get_challenge_by_token_hash("second-token") == second_challenge
    consumed = repo.consume_challenge(second_challenge, consumed_at=_utcnow())
    assert consumed.consumed_at is not None

    repo.add_session(
        AuthSession(
            user_id=user.id,
            refresh_token_hash="first-refresh",
            expires_at=_utcnow() + timedelta(days=1),
        )
    )
    second_session = repo.add_session(
        AuthSession(
            user_id=user.id,
            refresh_token_hash="second-refresh",
            expires_at=_utcnow() + timedelta(days=1),
        )
    )
    assert repo.get_session_by_refresh_hash("second-refresh") == second_session

    updated = repo.update_session(
        second_session.model_copy(update={"revoked_at": _utcnow()})
    )
    assert updated.revoked_at is not None
    assert first_challenge.token_hash == "first-token"


def test_missing_updates_and_lookups_raise() -> None:
    repo = InMemoryIdentityRepository()
    missing_user = _make_user()
    missing_challenge = _make_challenge()
    missing_session = AuthSession(
        user_id=uuid4(),
        refresh_token_hash="missing",
        expires_at=_utcnow() + timedelta(days=1),
    )

    with pytest.raises(UserNotFoundError):
        repo.update_user(missing_user)

    with pytest.raises(IdentityChallengeNotFoundError):
        repo.get_challenge(missing_challenge.id)

    with pytest.raises(IdentityChallengeNotFoundError):
        repo.update_challenge(missing_challenge)

    with pytest.raises(IdentityChallengeNotFoundError):
        repo.consume_challenge(missing_challenge, consumed_at=_utcnow())

    with pytest.raises(IdentitySessionNotFoundError):
        repo.get_session(missing_session.id)

    with pytest.raises(IdentitySessionNotFoundError):
        repo.update_session(missing_session)
