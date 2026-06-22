"""Tests for the first-party identity service (challenges, tokens, sessions)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
import jwt
import pytest
from orcheo.identity import (
    IdentityChallengeError,
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    InMemoryIdentityRepository,
)
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.service import IdentityService

SECRET = "test-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.test"


class CapturingSender:
    """Auth-challenge sender that records the last delivered email."""

    def __init__(self) -> None:
        self.sent: list[AuthChallengeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.sent.append(email)

    @property
    def last(self) -> AuthChallengeEmail:
        return self.sent[-1]

    def token_from_link(self) -> str:
        return self.last.magic_link_url.split("token=", 1)[1]


def _service(
    *, clock=None, **config_kwargs
) -> tuple[IdentityService, CapturingSender, InMemoryIdentityRepository]:
    repo = InMemoryIdentityRepository()
    sender = CapturingSender()
    config = IdentityConfig(
        jwt_secret=SECRET,
        issuer=ISSUER,
        audience="orcheo",
        verify_base_url="https://studio.test",
        **config_kwargs,
    )
    service = IdentityService(
        repo,
        email_sender=sender,
        config=config,
        clock=clock or (lambda: datetime.now(tz=UTC)),
    )
    return service, sender, repo


def test_start_challenge_sends_link_and_code() -> None:
    service, sender, _ = _service()
    service.start_challenge("Alice@Example.com")
    assert sender.last.to == "alice@example.com"
    assert "token=" in sender.last.magic_link_url
    assert sender.last.magic_link_url.startswith("https://studio.test/auth/verify")
    assert sender.last.otp_code.isdigit()


def test_start_challenge_preserves_safe_redirect() -> None:
    service, sender, _ = _service()
    service.start_challenge("Alice@Example.com", redirect_to="/workflows/123?tab=run")

    parsed = urlparse(sender.last.magic_link_url)
    query = parse_qs(parsed.query)
    assert query["redirect"] == ["/workflows/123?tab=run"]


def test_start_challenge_ignores_unsafe_redirect() -> None:
    service, sender, _ = _service()
    service.start_challenge("Alice@Example.com", redirect_to="//evil.test/path")

    parsed = urlparse(sender.last.magic_link_url)
    assert "redirect" not in parse_qs(parsed.query)


def test_start_challenge_rejects_malformed_email() -> None:
    service, _, _ = _service()
    with pytest.raises(ValueError, match="valid email"):
        service.start_challenge("not-an-email")


def test_verify_token_creates_user_and_mints_claims() -> None:
    service, sender, repo = _service()
    service.start_challenge("alice@example.com")
    result = service.verify_token(sender.token_from_link())

    assert result.user.email == "alice@example.com"
    assert result.user.email_verified is True
    assert repo.get_user_by_email("alice@example.com") is not None

    claims = jwt.decode(
        result.tokens.access_token, SECRET, algorithms=["HS256"], audience="orcheo"
    )
    assert claims["sub"] == str(result.user.id)
    assert claims["email"] == "alice@example.com"
    assert claims["email_verified"] is True
    assert claims["iss"] == ISSUER
    assert claims["scope"] == (
        "workflows:read workflows:write workflows:execute vault:read vault:write"
    )
    assert "workflows:execute" in claims["scopes"]


def test_verify_token_is_single_use() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    token = sender.token_from_link()
    service.verify_token(token)
    with pytest.raises(IdentityChallengeExpiredError):
        service.verify_token(token)


def test_verify_token_returning_user_is_reused() -> None:
    service, sender, repo = _service()
    service.start_challenge("alice@example.com")
    first = service.verify_token(sender.token_from_link())
    service.start_challenge("alice@example.com")
    second = service.verify_token(sender.token_from_link())
    assert first.user.id == second.user.id
    assert repo.get_user_by_email("alice@example.com") is not None


def test_verify_code_success() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    result = service.verify_code("alice@example.com", sender.last.otp_code)
    assert result.user.email == "alice@example.com"


def test_verify_code_locks_out_after_max_attempts() -> None:
    service, sender, _ = _service(otp_max_attempts=3)
    service.start_challenge("alice@example.com")
    for _ in range(2):
        with pytest.raises(IdentityChallengeError):
            service.verify_code("alice@example.com", "000000")
    # Third wrong attempt trips the lockout.
    with pytest.raises(IdentityChallengeLockedError):
        service.verify_code("alice@example.com", "000000")
    # Even the correct code is now locked out.
    with pytest.raises(IdentityChallengeLockedError):
        service.verify_code("alice@example.com", sender.last.otp_code)


def test_verify_expired_challenge_rejected() -> None:
    now = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
    service, sender, _ = _service(clock=lambda: now["t"], challenge_ttl_minutes=15)
    service.start_challenge("alice@example.com")
    token = sender.token_from_link()
    now["t"] = now["t"] + timedelta(minutes=16)
    with pytest.raises(IdentityChallengeExpiredError):
        service.verify_token(token)


def test_refresh_rotates_and_old_token_fails() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    issued = service.verify_token(sender.token_from_link()).tokens

    rotated = service.refresh(issued.refresh_token)
    assert rotated.refresh_token != issued.refresh_token
    assert rotated.access_token

    # The original refresh token is now consumed (rotated away).
    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh(issued.refresh_token)


def test_logout_revokes_sessions_and_blocks_refresh() -> None:
    service, sender, _ = _service()
    service.start_challenge("alice@example.com")
    result = service.verify_token(sender.token_from_link())

    revoked = service.logout(str(result.user.id))
    assert revoked == 1
    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh(result.tokens.refresh_token)


def test_verify_token_atomic_consume_failure_blocks_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sender, repo = _service()
    service.start_challenge("alice@example.com")
    token = sender.token_from_link()

    def consume_race(*args: object, **kwargs: object) -> object:
        raise IdentityChallengeNotFoundError("already-consumed")

    monkeypatch.setattr(repo, "consume_challenge", consume_race)

    with pytest.raises(IdentityChallengeExpiredError):
        service.verify_token(token)

    assert repo.get_user_by_email("alice@example.com") is None
