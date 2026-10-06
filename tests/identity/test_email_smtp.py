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
    PasskeyNoticeEmail,
    SmtpEmailSender,
    SmtpSettings,
    build_email_sender,
    render_passkey_notice_email,
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
            to="alice@example.com", otp_code="123456", expires_in_minutes=15
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user", "pass")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "alice@example.com"
    assert smtp.sent["Subject"] == "123456 is your Orcheo sign-in code"
    html_body = smtp.sent.get_body(("html",)).get_content()
    text_body = smtp.sent.get_body(("plain",)).get_content()
    assert "letter-spacing:8px" in html_body
    assert ">123456</td>" in html_body
    assert "href" not in html_body
    assert text_body.startswith("Your Orcheo sign-in code is 123456.")
    assert "expires in 15 minutes" in text_body


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


def test_smtp_sends_passkey_notices() -> None:
    sender = SmtpEmailSender(_settings())
    sender.send_passkey_notice(
        PasskeyNoticeEmail(
            to="alice@example.com",
            passkey_name="Work <laptop>",
            action="added",
            occurred_at=datetime(2026, 10, 6, 12, 30, tzinfo=UTC),
        )
    )
    smtp = FakeSMTP.instances[-1]
    assert smtp.sent is not None
    assert smtp.sent["To"] == "alice@example.com"
    assert smtp.sent["Subject"] == "A passkey was added to your Orcheo account"
    html_body = smtp.sent.get_body(("html",)).get_content()
    text_body = smtp.sent.get_body(("plain",)).get_content()
    assert "Work &lt;laptop&gt;" in html_body
    assert "<laptop>" not in html_body
    assert text_body.startswith(
        'The passkey "Work <laptop>" was added to your Orcheo account on '
        "2026-10-06 12:30 UTC."
    )
    assert "If you did not do this" in text_body


def test_passkey_removal_notice_wording() -> None:
    rendered = render_passkey_notice_email(
        PasskeyNoticeEmail(
            to="alice@example.com",
            passkey_name="Phone",
            action="removed",
            occurred_at=datetime(2026, 10, 6, 12, 30, tzinfo=UTC),
        )
    )
    assert rendered.subject == "A passkey was removed from your Orcheo account"
    assert "was removed from your Orcheo account" in rendered.text


def test_logging_sender_logs_passkey_notices(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="orcheo.workspace.email"):
        LoggingInvitationEmailSender().send_passkey_notice(
            PasskeyNoticeEmail(
                to="alice@example.com",
                passkey_name="Phone",
                action="removed",
                occurred_at=datetime(2026, 10, 6, 12, 30, tzinfo=UTC),
            )
        )
    assert "Passkey 'Phone' was removed for alice@example.com" in caplog.text
