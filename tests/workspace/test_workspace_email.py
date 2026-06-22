"""Tests for the transactional email senders and the provider factory."""

from __future__ import annotations
import logging
from datetime import UTC, datetime
from orcheo.workspace.email import (
    InvitationEmail,
    LoggingInvitationEmailSender,
    SmtpEmailSender,
    SmtpSettings,
    build_email_sender,
)


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
