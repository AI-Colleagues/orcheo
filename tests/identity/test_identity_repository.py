"""Tests for the in-memory identity repository and domain models."""

from __future__ import annotations
from datetime import timedelta
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
