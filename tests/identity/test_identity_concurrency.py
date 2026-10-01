"""Concurrent auth regressions, optionally repeated against isolated PostgreSQL.

Only ORCHEO_TEST_POSTGRES_DSN enables database tests. Each test creates and drops
its own schema, never using the application's configured PostgreSQL database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from orcheo.identity import (
    AuthEmailChallenge,
    AuthSession,
    IdentityChallengeError,
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    InMemoryIdentityRepository,
    PostgresIdentityRepository,
    User,
)
from orcheo.identity.repository import IdentityRepository
from orcheo.postgres_pools import connection_kwargs, pool_kwargs
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.service import IdentityService, IssuedTokens
from orcheo_backend.app.identity.tokens import hash_secret
from orcheo.workspace.email import LoggingInvitationEmailSender


NOW = datetime(2026, 10, 1, tzinfo=UTC)
WORKERS = 8
MAX_ATTEMPTS = 3


@pytest.fixture(params=["memory", "postgres"])
def repository(request: pytest.FixtureRequest) -> Iterator[IdentityRepository]:
    """Run identical races against both storage implementations."""
    if request.param == "memory":
        yield InMemoryIdentityRepository()
        return
    dsn = os.environ.get("ORCHEO_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("ORCHEO_TEST_POSTGRES_DSN is not set to an isolated database.")
    schema = f"identity_race_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with ConnectionPool(
                dsn,
                min_size=WORKERS,
                max_size=WORKERS,
                kwargs={
                    **connection_kwargs(autocommit=False, row_factory=dict_row),
                    "options": f"-csearch_path={schema}",
                },
                **pool_kwargs(),
            ) as pool:
                pool.wait(timeout=5)
                repo = PostgresIdentityRepository.__new__(PostgresIdentityRepository)
                repo._pool = pool
                repo._statement_timeout_ms = 10_000
                repo._lock_timeout_ms = 3_000
                repo._ensure_schema()
                yield repo
        finally:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def service(repository: IdentityRepository) -> IdentityService:
    return IdentityService(
        repository,
        email_sender=LoggingInvitationEmailSender(),
        config=IdentityConfig(
            jwt_secret="concurrency-test", otp_max_attempts=MAX_ATTEMPTS
        ),
        clock=lambda: NOW,
    )


def _challenge(repository: IdentityRepository) -> AuthEmailChallenge:
    return repository.add_challenge(
        AuthEmailChallenge(
            email="race@example.com",
            code_hash=hash_secret("123456"),
            expires_at=NOW + timedelta(minutes=15),
        )
    )


@pytest.mark.parametrize("oauth", [False, True])
def test_same_refresh_token_rotates_only_once(
    repository: IdentityRepository,
    service: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
    oauth: bool,
) -> None:
    user = repository.create_user(User(email="race@example.com", email_verified=True))
    session = repository.add_session(
        AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_secret("original"),
            expires_at=NOW + timedelta(days=1),
            oauth_client_id="client" if oauth else None,
            scopes=["workflows:read"] if oauth else None,
        )
    )
    lookup = repository.get_session_by_refresh_hash
    barrier = Barrier(WORKERS)

    def synchronized_lookup(token_hash: str) -> AuthSession:
        result = lookup(token_hash)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(repository, "get_session_by_refresh_hash", synchronized_lookup)

    def refresh() -> IssuedTokens | None:
        try:
            if oauth:
                return service.refresh_oauth_session(
                    "original", client_id="client", scopes=["workflows:read"]
                )
            return service.refresh("original")
        except IdentitySessionNotFoundError:
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        results = list(executor.map(lambda _: refresh(), range(WORKERS)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert repository.get_session(session.id).refresh_token_hash == hash_secret(
        winners[0].refresh_token
    )
    monkeypatch.setattr(repository, "get_session_by_refresh_hash", lookup)
    if oauth:
        assert service.refresh_oauth_session(
            winners[0].refresh_token, client_id="client", scopes=["workflows:read"]
        )
    else:
        assert service.refresh(winners[0].refresh_token)


def test_refresh_cannot_succeed_after_concurrent_logout(
    repository: IdentityRepository,
    service: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = repository.create_user(User(email="race@example.com"))
    session = repository.add_session(
        AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_secret("original"),
            expires_at=NOW + timedelta(days=1),
        )
    )
    get_user = repository.get_user

    def logout_before_rotation(user_id: Any) -> User:
        result = get_user(user_id)
        repository.revoke_sessions_for_user(user_id)
        return result

    monkeypatch.setattr(repository, "get_user", logout_before_rotation)
    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh("original")
    stored = repository.get_session(session.id)
    assert stored.revoked_at is not None
    assert stored.refresh_token_hash == hash_secret("original")


def test_parallel_wrong_codes_do_not_lose_attempts(
    repository: IdentityRepository,
    service: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge = _challenge(repository)
    lookup = repository.find_active_challenge_for_email
    barrier = Barrier(WORKERS)

    def synchronized_lookup(email: str, *, now: datetime) -> AuthEmailChallenge | None:
        result = lookup(email, now=now)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(
        repository, "find_active_challenge_for_email", synchronized_lookup
    )

    def guess() -> type[Exception]:
        try:
            service.verify_code(challenge.email, "wrong")
        except IdentityChallengeError as exc:
            return type(exc)
        pytest.fail("Wrong code was accepted")

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        results = list(executor.map(lambda _: guess(), range(WORKERS)))
    assert results.count(IdentityChallengeLockedError) == WORKERS - MAX_ATTEMPTS + 1
    assert repository.get_challenge(challenge.id).attempts == MAX_ATTEMPTS
    monkeypatch.setattr(repository, "find_active_challenge_for_email", lookup)
    with pytest.raises(IdentityChallengeLockedError):
        service.verify_code(challenge.email, "123456")


def test_correct_code_cannot_redeem_after_concurrent_lockout(
    repository: IdentityRepository,
    service: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge = _challenge(repository)
    consume = repository.consume_challenge
    ready = Event()
    release = Event()

    def delayed_consume(*args: Any, **kwargs: Any) -> AuthEmailChallenge:
        ready.set()
        assert release.wait(timeout=5)
        return consume(*args, **kwargs)

    monkeypatch.setattr(repository, "consume_challenge", delayed_consume)
    with ThreadPoolExecutor(max_workers=1) as executor:
        correct = executor.submit(service.verify_code, challenge.email, "123456")
        try:
            assert ready.wait(timeout=5)
            for _ in range(MAX_ATTEMPTS):
                with pytest.raises(IdentityChallengeError):
                    service.verify_code(challenge.email, "wrong")
        finally:
            release.set()
        with pytest.raises(IdentityChallengeLockedError):
            correct.result(timeout=5)
    assert repository.get_challenge(challenge.id).consumed_at is None
    assert repository.get_user_by_email(challenge.email) is None


def test_challenge_cannot_redeem_after_expiring_between_lookup_and_consume(
    repository: IdentityRepository,
) -> None:
    challenge = _challenge(repository)
    with pytest.raises(IdentityChallengeError):
        repository.consume_challenge(
            challenge,
            consumed_at=challenge.expires_at,
            max_attempts=MAX_ATTEMPTS,
        )
    assert repository.get_challenge(challenge.id).consumed_at is None


@pytest.mark.parametrize("operation", ["consume", "increment"])
@pytest.mark.parametrize("state", ["missing", "consumed", "expired", "locked"])
def test_unavailable_challenge_errors_match_between_stores(
    repository: IdentityRepository, operation: str, state: str
) -> None:
    """A stale verification must distinguish lockout from expiry or consumption."""
    challenge = _challenge(repository)
    now = NOW
    challenge_id = challenge.id
    if state == "missing":
        challenge_id = uuid4()
        challenge = challenge.model_copy(update={"id": challenge_id})
    elif state == "consumed":
        repository.consume_challenge(challenge, consumed_at=NOW)
    elif state == "expired":
        now = challenge.expires_at
    else:
        repository.update_challenge(
            challenge.model_copy(update={"attempts": MAX_ATTEMPTS})
        )
    expected = (
        IdentityChallengeLockedError
        if state == "locked"
        else IdentityChallengeNotFoundError
    )
    with pytest.raises(expected):
        if operation == "consume":
            repository.consume_challenge(
                challenge, consumed_at=now, max_attempts=MAX_ATTEMPTS
            )
        else:
            repository.increment_challenge_attempts(
                challenge_id, now=now, max_attempts=MAX_ATTEMPTS
            )
