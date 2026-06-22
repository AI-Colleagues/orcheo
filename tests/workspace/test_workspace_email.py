"""Tests for the transactional email senders and the provider factory."""

from __future__ import annotations
import logging
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


def _email() -> InvitationEmail:
    return InvitationEmail(
        to="invitee@example.com",
        workspace_name="Acme",
        role="editor",
        accept_url="https://studio.example.com/invitations/accept?token=abc123",
        expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_factory_returns_logging_sender_without_smtp() -> None:
    assert isinstance(build_email_sender(), LoggingInvitationEmailSender)
    blank = SmtpSettings(host="   ")
    assert isinstance(build_email_sender(smtp=blank), LoggingInvitationEmailSender)


def test_factory_returns_smtp_sender_with_host() -> None:
    sender = build_email_sender(smtp=SmtpSettings(host="smtp.example.com"))
    assert isinstance(sender, SmtpEmailSender)


def test_logging_sender_logs_accept_url(caplog) -> None:
    with caplog.at_level(logging.INFO):
        LoggingInvitationEmailSender().send_invitation(_email())
    assert "token=abc123" in caplog.text


def test_logging_sender_logs_auth_challenge(caplog) -> None:
    with caplog.at_level(logging.INFO):
        LoggingInvitationEmailSender().send_auth_challenge(
            AuthChallengeEmail(
                to="invitee@example.com",
                magic_link_url="https://studio.example.com/auth/verify?token=abc123",
                otp_code="654321",
                expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            )
        )
    assert "654321" in caplog.text
    assert "auth/verify?token=abc123" in caplog.text


def test_smtp_sender_covers_renderers_and_tls_branches() -> None:
    sender = SmtpEmailSender(
        SmtpSettings(
            host="smtp.test",
            port=2525,
            username="user",
            password="pass",
            use_tls=True,
        )
    )
    sender.send_invitation(_email())
    sender.send_auth_challenge(
        AuthChallengeEmail(
            to="invitee@example.com",
            magic_link_url="https://studio.example.com/auth/verify?token=abc123",
            otp_code="654321",
            expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user", "pass")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "invitee@example.com"


def test_smtp_sender_skips_tls_and_login_when_disabled() -> None:
    sender = SmtpEmailSender(
        SmtpSettings(
            host="smtp.test",
            port=2525,
            use_tls=False,
        )
    )
    sender.send_invitation(_email())
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is False
    assert smtp.logged_in is None
