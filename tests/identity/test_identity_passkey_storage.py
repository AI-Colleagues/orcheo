"""Tests for passkey storage in the in-memory identity repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from orcheo.identity import (
    InMemoryIdentityRepository,
    Passkey,
    PasskeyAlreadyRegisteredError,
    PasskeyCeremony,
    PasskeyChallenge,
    PasskeyChallengeNotFoundError,
    PasskeyNotFoundError,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _passkey(user_id, credential_id: str = "cred", **fields) -> Passkey:
    return Passkey(
        user_id=user_id,
        credential_id=credential_id,
        public_key="key",
        name="Key",
        **fields,
    )


def test_passkeys_are_listed_oldest_first_and_found_by_credential() -> None:
    repo = InMemoryIdentityRepository()
    owner, other = uuid4(), uuid4()
    newer = repo.add_passkey(_passkey(owner, "b", created_at=NOW))
    older = repo.add_passkey(_passkey(owner, "a", created_at=NOW - timedelta(days=1)))
    repo.add_passkey(_passkey(other, "c"))

    assert repo.list_passkeys(owner) == [older, newer]
    assert repo.get_passkey_by_credential_id("b") == newer
    assert repo.get_passkey_by_credential_id("missing") is None
    with pytest.raises(PasskeyAlreadyRegisteredError):
        repo.add_passkey(_passkey(other, "a"))


def test_recording_use_never_lowers_the_signature_counter() -> None:
    repo = InMemoryIdentityRepository()
    passkey = repo.add_passkey(_passkey(uuid4(), sign_count=7))

    used = repo.record_passkey_use(
        passkey.id, sign_count=3, backed_up=True, used_at=NOW
    )

    assert used.sign_count == 7
    assert used.backed_up is True
    assert used.last_used_at == NOW
    assert (
        repo.record_passkey_use(
            passkey.id, sign_count=9, backed_up=False, used_at=NOW
        ).sign_count
        == 9
    )
    with pytest.raises(PasskeyNotFoundError):
        repo.record_passkey_use(uuid4(), sign_count=1, backed_up=False, used_at=NOW)


def test_only_the_owner_can_rename_or_delete_a_passkey() -> None:
    repo = InMemoryIdentityRepository()
    owner, other = uuid4(), uuid4()
    passkey = repo.add_passkey(_passkey(owner))

    with pytest.raises(PasskeyNotFoundError):
        repo.rename_passkey(other, passkey.id, "Mine")
    with pytest.raises(PasskeyNotFoundError):
        repo.delete_passkey(other, passkey.id)
    with pytest.raises(PasskeyNotFoundError):
        repo.delete_passkey(owner, uuid4())

    assert repo.rename_passkey(owner, passkey.id, "Phone").name == "Phone"
    assert repo.delete_passkey(owner, passkey.id).name == "Phone"
    assert repo.list_passkeys(owner) == []


def _challenge(ceremony: PasskeyCeremony, **fields) -> PasskeyChallenge:
    fields.setdefault("created_at", NOW)
    fields.setdefault("expires_at", NOW + timedelta(minutes=5))
    return PasskeyChallenge(ceremony=ceremony, challenge="abc", **fields)


def test_challenges_are_bound_to_their_ceremony_and_user() -> None:
    repo = InMemoryIdentityRepository()
    user_id = uuid4()
    sign_in = repo.add_passkey_challenge(_challenge(PasskeyCeremony.AUTHENTICATION))
    registration = repo.add_passkey_challenge(
        _challenge(PasskeyCeremony.REGISTRATION, user_id=user_id)
    )

    for challenge_id, ceremony, owner in (
        (sign_in.id, PasskeyCeremony.REGISTRATION, None),
        (registration.id, PasskeyCeremony.REGISTRATION, uuid4()),
        (registration.id, PasskeyCeremony.REGISTRATION, None),
        (uuid4(), PasskeyCeremony.AUTHENTICATION, None),
    ):
        with pytest.raises(PasskeyChallengeNotFoundError):
            repo.consume_passkey_challenge(
                challenge_id, ceremony=ceremony, user_id=owner, now=NOW
            )

    assert (
        repo.consume_passkey_challenge(
            registration.id,
            ceremony=PasskeyCeremony.REGISTRATION,
            user_id=user_id,
            now=NOW,
        )
        == registration
    )
    assert (
        repo.consume_passkey_challenge(
            sign_in.id, ceremony=PasskeyCeremony.AUTHENTICATION, user_id=None, now=NOW
        )
        == sign_in
    )
    with pytest.raises(PasskeyChallengeNotFoundError):
        repo.consume_passkey_challenge(
            sign_in.id, ceremony=PasskeyCeremony.AUTHENTICATION, user_id=None, now=NOW
        )


def test_expired_challenges_are_refused_and_purged() -> None:
    repo = InMemoryIdentityRepository()
    stale = repo.add_passkey_challenge(_challenge(PasskeyCeremony.AUTHENTICATION))

    with pytest.raises(PasskeyChallengeNotFoundError):
        repo.consume_passkey_challenge(
            stale.id,
            ceremony=PasskeyCeremony.AUTHENTICATION,
            user_id=None,
            now=stale.expires_at,
        )
    fresh = repo.add_passkey_challenge(
        _challenge(
            PasskeyCeremony.AUTHENTICATION,
            created_at=stale.expires_at,
            expires_at=stale.expires_at + timedelta(minutes=5),
        )
    )

    assert set(repo._passkey_challenges) == {fresh.id}
