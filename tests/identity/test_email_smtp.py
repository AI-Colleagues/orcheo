"""Tests for the SMTP transactional email sender and builder selection."""

from __future__ import annotations
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
import pytest
from orcheo.workspace.email import (
    AuthChallengeEmail,
    InvitationEmail,
    LoggingInvitationEmailSender,
    SmtpEmailSender,
    SmtpSettings,
    build_email_sender,
)


class FakeSMTP:
    """Minimal stand-in for ``smtplib.SMTP`` capturing the sent message."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: EmailMessage | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent = message


@pytest.fixture(autouse=True)
def _patch_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr("orcheo.workspace.email.smtplib.SMTP", FakeSMTP)


def _settings() -> SmtpSettings:
    return SmtpSettings(
        host="smtp.test",
        port=587,
        username="user",
        password="pass",
        from_email="no-reply@orcheo.cloud",
        use_tls=True,
    )


def test_smtp_sends_auth_challenge_with_tls_and_login() -> None:
    sender = SmtpEmailSender(_settings())
    sender.send_auth_challenge(
        AuthChallengeEmail(
            to="alice@example.com",
            magic_link_url="https://studio.test/auth/verify?token=abc",
            otp_code="123456",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user", "pass")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "alice@example.com"
    assert "123456" in smtp.sent.get_body(("html",)).get_content()


def test_smtp_sends_invitation() -> None:
    sender = SmtpEmailSender(_settings())
    sender.send_invitation(
        InvitationEmail(
            to="bob@example.com",
            workspace_name="Acme",
            role="editor",
            accept_url="https://studio.test/invitations/accept?token=xyz",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.sent is not None
    assert "Acme" in smtp.sent["Subject"]


def test_smtp_skips_tls_and_login_when_disabled_or_missing_credentials() -> None:
    sender = SmtpEmailSender(
        SmtpSettings(
            host="smtp.test",
            port=2525,
            use_tls=False,
            username=None,
            password=None,
        )
    )
    sender.send_invitation(
        InvitationEmail(
            to="carol@example.com",
            workspace_name="Carol",
            role="viewer",
            accept_url="https://studio.test/invitations/accept?token=xyz",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is False
    assert smtp.logged_in is None
    assert smtp.sent is not None


def test_builder_uses_smtp_when_configured_else_logging() -> None:
    assert isinstance(build_email_sender(smtp=_settings()), SmtpEmailSender)
    assert isinstance(build_email_sender(), LoggingInvitationEmailSender)
