"""Transactional email ports, a logging default, and an SMTP sender.

The transactional email abstraction is shared by workspace invitations,
first-party sign-in codes, and passkey security notices. Production
deployments use the
:class:`SmtpEmailSender`; local/self-host setups fall back to the
:class:`LoggingInvitationEmailSender`, which logs the link/code instead of
delivering email. SMTP is the sole production transport.
"""

from __future__ import annotations
import html
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Literal, Protocol


__all__ = [
    "DEFAULT_INVITE_FROM_EMAIL",
    "AuthChallengeEmail",
    "AuthChallengeEmailSender",
    "InvitationEmail",
    "InvitationEmailSender",
    "LoggingInvitationEmailSender",
    "PasskeyNoticeEmail",
    "PasskeyNoticeEmailSender",
    "RenderedEmail",
    "SmtpEmailSender",
    "SmtpSettings",
    "TransactionalEmailSender",
    "build_email_sender",
    "render_passkey_notice_email",
    "render_sign_in_code_email",
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
    """A one-time sign-in code to deliver to a recipient."""

    to: str
    otp_code: str
    expires_in_minutes: int


@dataclass(frozen=True)
class PasskeyNoticeEmail:
    """Security notice that a passkey was added to or removed from an account."""

    to: str
    passkey_name: str
    action: Literal["added", "removed"]
    occurred_at: datetime


class InvitationEmailSender(Protocol):
    """Port for delivering workspace invitation emails."""

    def send_invitation(self, email: InvitationEmail) -> None:
        """Deliver a single invitation email."""


class AuthChallengeEmailSender(Protocol):
    """Port for delivering passwordless auth challenge emails."""

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        """Deliver a single sign-in code email."""


class PasskeyNoticeEmailSender(Protocol):
    """Port for delivering passkey security notices."""

    def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
        """Deliver a single passkey security notice."""


class TransactionalEmailSender(
    InvitationEmailSender, AuthChallengeEmailSender, PasskeyNoticeEmailSender, Protocol
):
    """Combined transactional email port for invitations, codes and notices."""


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
        """Log the sign-in code."""
        logger.info(
            "Sign-in code for %s (expires in %d minutes): %s",
            email.to,
            email.expires_in_minutes,
            email.otp_code,
        )

    def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
        """Log the passkey security notice."""
        logger.info(
            "Passkey %r was %s for %s at %s",
            email.passkey_name,
            email.action,
            email.to,
            email.occurred_at.isoformat(),
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


# Mail clients strip stylesheets and custom properties, so the sign-in email's
# colours are literal and inline (the GatherEasy transactional style).
_EMAIL_INK = "#1c1c1a"
_EMAIL_MUTED = "#6b6b66"
_EMAIL_SURFACE = "#faf9f6"
_EMAIL_FONT = "'IBM Plex Sans',Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class RenderedEmail:
    """Subject plus plain-text and HTML bodies of one email."""

    subject: str
    text: str
    html: str


def render_sign_in_code_email(email: AuthChallengeEmail) -> RenderedEmail:
    """Build the mail carrying one sign-in code.

    Transactional auth mail: no link, button or unsubscribe footer. The code
    is set large and letter-spaced because the only thing anyone does with
    this mail is read the digits off it, often on a phone, with the sign-in
    form already open.
    """
    lead = "Your Orcheo sign-in code is"
    expiry = (
        f"It expires in {email.expires_in_minutes} minutes. If you did not try "
        "to sign in, you can ignore this email."
    )
    code = html.escape(email.otp_code)
    body = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:{_EMAIL_SURFACE};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_EMAIL_SURFACE};padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:16px;padding:32px;font-family:{_EMAIL_FONT};color:{_EMAIL_INK};">
            <tr>
              <td style="font-size:16px;line-height:1.5;">{lead}</td>
            </tr>
            <tr>
              <td style="font-size:32px;font-weight:700;letter-spacing:8px;padding:12px 0 20px;">{code}</td>
            </tr>
            <tr>
              <td style="font-size:13px;line-height:1.6;color:{_EMAIL_MUTED};">{html.escape(expiry)}</td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""  # noqa: E501 - inline email markup
    return RenderedEmail(
        subject=f"{email.otp_code} is your Orcheo sign-in code",
        text=f"{lead} {email.otp_code}.\n\n{expiry}",
        html=body,
    )


def render_passkey_notice_email(email: PasskeyNoticeEmail) -> RenderedEmail:
    """Build the security notice for a passkey being added or removed.

    The account owner may not have made the change themselves, so the mail
    says what to do if they did not.
    """
    added = email.action == "added"
    change = "added to" if added else "removed from"
    when = email.occurred_at.strftime("%Y-%m-%d %H:%M UTC")
    lead = (
        f'The passkey "{email.passkey_name}" was {change} your Orcheo account '
        f"on {when}."
    )
    advice = (
        "If you did not do this, sign in with a code sent to this address, "
        "remove any passkeys you do not recognize from your profile, and sign "
        "out to end every session."
    )
    body = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:{_EMAIL_SURFACE};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_EMAIL_SURFACE};padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:16px;padding:32px;font-family:{_EMAIL_FONT};color:{_EMAIL_INK};">
            <tr>
              <td style="font-size:16px;line-height:1.5;padding-bottom:16px;">{html.escape(lead)}</td>
            </tr>
            <tr>
              <td style="font-size:13px;line-height:1.6;color:{_EMAIL_MUTED};">{html.escape(advice)}</td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""  # noqa: E501 - inline email markup
    return RenderedEmail(
        subject=f"A passkey was {change} your Orcheo account",
        text=f"{lead}\n\n{advice}",
        html=body,
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

    Implements the invitation, auth-challenge and passkey-notice ports. Raises
    on a hard SMTP failure so the calling service surfaces delivery problems.
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
        rendered = render_sign_in_code_email(email)
        self._send(email.to, rendered.subject, rendered.html, text_body=rendered.text)

    def send_passkey_notice(self, email: PasskeyNoticeEmail) -> None:
        """Send a passkey security notice over SMTP."""
        rendered = render_passkey_notice_email(email)
        self._send(email.to, rendered.subject, rendered.html, text_body=rendered.text)

    def _send(
        self,
        to: str,
        subject: str,
        html_body: str,
        *,
        text_body: str = "This message requires an HTML-capable email client.",
    ) -> None:
        message = EmailMessage()
        message["From"] = self._settings.from_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text_body)
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
