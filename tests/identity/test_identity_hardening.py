"""Hardening: anti-enumeration, OTP lockout, and identity telemetry."""

from __future__ import annotations
from datetime import UTC, datetime
import pytest
from orcheo.identity import (
    IdentityChallengeError,
    IdentityChallengeLockedError,
    InMemoryIdentityRepository,
)
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication.telemetry import AuthTelemetry
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.service import IdentityService

SECRET = "hardening-secret"  # noqa: S105 - test fixture


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[AuthChallengeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.sent.append(email)


class FailingSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        raise RuntimeError("smtp down")


def _service(sender, *, telemetry=None, **cfg):
    repo = InMemoryIdentityRepository()
    service = IdentityService(
        repo,
        email_sender=sender,
        config=IdentityConfig(jwt_secret=SECRET, **cfg),
        telemetry=telemetry,
        clock=lambda: datetime.now(tz=UTC),
    )
    return service, repo


def test_start_challenge_is_anti_enumerative() -> None:
    """A challenge is created and sent whether or not the account exists."""
    sender = CapturingSender()
    service, repo = _service(sender)

    # Unknown email.
    service.start_challenge("new@example.com")
    # Known email (create via a first verify).
    token = sender.sent[-1].magic_link_url.split("token=", 1)[1]
    service.verify_token(token)
    assert repo.get_user_by_email("new@example.com") is not None

    before = len(sender.sent)
    service.start_challenge("new@example.com")  # existing user
    service.start_challenge("stranger@example.com")  # non-existent user
    # Both paths create+send a challenge identically (no existence oracle).
    assert len(sender.sent) == before + 2


def test_otp_lockout_records_and_blocks() -> None:
    telemetry = AuthTelemetry()
    sender = CapturingSender()
    service, _ = _service(sender, telemetry=telemetry, otp_max_attempts=2)
    service.start_challenge("alice@example.com")

    with pytest.raises(IdentityChallengeError):
        service.verify_code("alice@example.com", "000000")
    with pytest.raises(IdentityChallengeLockedError):
        service.verify_code("alice@example.com", "000000")


def test_telemetry_records_signup_and_login() -> None:
    telemetry = AuthTelemetry()
    sender = CapturingSender()
    service, _ = _service(sender, telemetry=telemetry)

    service.start_challenge("alice@example.com")
    token = sender.sent[-1].magic_link_url.split("token=", 1)[1]
    service.verify_token(token)

    metrics = telemetry.metrics()
    assert metrics.get("auth.signup:success") == 1
    assert metrics.get("auth.login:success") == 1
    assert metrics.get("auth.challenge_sent:success") == 1

    # A returning login records a login but not another signup.
    service.start_challenge("alice@example.com")
    token2 = sender.sent[-1].magic_link_url.split("token=", 1)[1]
    service.verify_token(token2)
    metrics = telemetry.metrics()
    assert metrics.get("auth.signup:success") == 1
    assert metrics.get("auth.login:success") == 2


def test_telemetry_records_delivery_failure() -> None:
    telemetry = AuthTelemetry()
    service, _ = _service(FailingSender(), telemetry=telemetry)
    with pytest.raises(RuntimeError):
        service.start_challenge("alice@example.com")
    assert telemetry.metrics().get("auth.email_delivery_failure:failure") == 1


def test_telemetry_records_verification_expiry() -> None:
    telemetry = AuthTelemetry()
    sender = CapturingSender()
    service, _ = _service(sender, telemetry=telemetry)
    with pytest.raises(IdentityChallengeError):
        service.verify_code("nobody@example.com", "123456")
    assert telemetry.metrics().get("auth.verify_expired:failure") == 1
