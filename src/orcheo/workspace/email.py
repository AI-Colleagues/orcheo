"""Transactional email ports, a logging default, and an SMTP sender.

The transactional email abstraction is shared by two callers: workspace
invitations and first-party auth challenges (magic link + OTP). Production
deployments use the :class:`SmtpEmailSender`; local/self-host setups fall back
to the :class:`LoggingInvitationEmailSender`, which logs the link/code instead
of delivering email. SMTP is the sole production transport.
"""

from __future__ import annotations
import html
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol


__all__ = [
    "DEFAULT_INVITE_FROM_EMAIL",
    "AuthChallengeEmail",
    "AuthChallengeEmailSender",
    "InvitationEmail",
    "InvitationEmailSender",
    "LoggingInvitationEmailSender",
    "SmtpEmailSender",
    "SmtpSettings",
    "TransactionalEmailSender",
    "build_email_sender",
]

logger = logging.getLogger(__name__)

DEFAULT_INVITE_FROM_EMAIL = "no-reply@orcheo.cloud"


@dataclass(frozen=True)
class InvitationEmail:
    """Rendered invitation ready to be delivered to a recipient."""

    to: str
    workspace_name: str
    role: str
    accept_url: str
    expires_at: datetime
    invited_by: str | None = None


@dataclass(frozen=True)
class AuthChallengeEmail:
    """Rendered passwordless auth challenge (magic link + OTP code)."""

    to: str
    magic_link_url: str
    otp_code: str
    expires_at: datetime


class InvitationEmailSender(Protocol):
    """Port for delivering workspace invitation emails."""

    def send_invitation(self, email: InvitationEmail) -> None:
        """Deliver a single invitation email."""


class AuthChallengeEmailSender(Protocol):
    """Port for delivering passwordless auth challenge emails."""

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        """Deliver a single auth challenge email (magic link + OTP)."""


class TransactionalEmailSender(
    InvitationEmailSender, AuthChallengeEmailSender, Protocol
):
    """Combined transactional email port covering invitations and challenges."""


class LoggingInvitationEmailSender:
    """Default sender that logs links/codes instead of sending email.

    Used for local development and self-hosting where no transactional email
    provider is configured. The acceptance URL / magic link and OTP are logged
    so operators can relay them manually.
    """

    def send_invitation(self, email: InvitationEmail) -> None:
        """Log the invitation acceptance link."""
        logger.info(
            "Workspace invitation for %s to %r (role=%s) — accept by %s: %s",
            email.to,
            email.workspace_name,
            email.role,
            email.expires_at.isoformat(),
            email.accept_url,
        )

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        """Log the magic link and OTP code."""
        logger.info(
            "Auth challenge for %s — expires %s — code %s: %s",
            email.to,
            email.expires_at.isoformat(),
            email.otp_code,
            email.magic_link_url,
        )


def _render_invitation_html(email: InvitationEmail) -> str:
    """Render a minimal, provider-agnostic HTML body for an invitation."""
    workspace = html.escape(email.workspace_name)
    role = html.escape(email.role)
    url = html.escape(email.accept_url, quote=True)
    expires = html.escape(email.expires_at.strftime("%Y-%m-%d %H:%M UTC"))
    return (
        f"<p>You've been invited to join <strong>{workspace}</strong> on Orcheo "
        f"as <strong>{role}</strong>.</p>"
        f'<p><a href="{url}">Accept your invitation</a></p>'
        f"<p>This link expires on {expires}. If you weren't expecting this, you "
        f"can ignore this email.</p>"
    )


def _render_auth_challenge_html(email: AuthChallengeEmail) -> str:
    """Render a minimal, provider-agnostic HTML body for an auth challenge."""
    url = html.escape(email.magic_link_url, quote=True)
    code = html.escape(email.otp_code)
    expires = html.escape(email.expires_at.strftime("%Y-%m-%d %H:%M UTC"))
    return (
        "<p>Use the link below to sign in to Orcheo:</p>"
        f'<p><a href="{url}">Sign in to Orcheo</a></p>'
        f"<p>Or enter this code: <strong>{code}</strong></p>"
        f"<p>This link and code expire on {expires}. If you didn't request this, "
        f"you can ignore this email.</p>"
    )


@dataclass(frozen=True)
class SmtpSettings:
    """Connection settings for the SMTP transactional email transport."""

    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_email: str = DEFAULT_INVITE_FROM_EMAIL
    use_tls: bool = True
    timeout: float = 10.0


class SmtpEmailSender:
    """Deliver transactional email over SMTP (the production transport).

    Implements both the invitation and auth-challenge ports. Raises on a hard
    SMTP failure so the calling service surfaces delivery problems.
    """

    def __init__(self, settings: SmtpSettings) -> None:
        """Bind the sender to SMTP connection settings."""
        self._settings = settings

    def send_invitation(self, email: InvitationEmail) -> None:
        """Send a workspace invitation email over SMTP."""
        subject = f"You've been invited to {email.workspace_name} on Orcheo"
        self._send(email.to, subject, _render_invitation_html(email))

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        """Send a passwordless auth challenge email over SMTP."""
        self._send(email.to, "Sign in to Orcheo", _render_auth_challenge_html(email))

    def _send(self, to: str, subject: str, html_body: str) -> None:
        message = EmailMessage()
        message["From"] = self._settings.from_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content("This message requires an HTML-capable email client.")
        message.add_alternative(html_body, subtype="html")

        settings = self._settings
        with smtplib.SMTP(
            settings.host, settings.port, timeout=settings.timeout
        ) as smtp:
            if settings.use_tls:
                smtp.starttls()
            if settings.username and settings.password:
                smtp.login(settings.username, settings.password)
            smtp.send_message(message)


def build_email_sender(
    *,
    smtp: SmtpSettings | None = None,
) -> TransactionalEmailSender:
    """Return the configured transactional sender.

    Uses the SMTP sender when an SMTP host is configured, otherwise the logging
    sender. Deployments opt into real delivery purely through configuration.
    """
    if smtp is not None and smtp.host.strip():
        return SmtpEmailSender(smtp)
    return LoggingInvitationEmailSender()
