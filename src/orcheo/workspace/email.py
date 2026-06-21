"""Invitation email delivery port, a logging default, and a Resend sender."""

from __future__ import annotations
import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
import httpx


__all__ = [
    "DEFAULT_INVITE_FROM_EMAIL",
    "InvitationEmail",
    "InvitationEmailSender",
    "LoggingInvitationEmailSender",
    "ResendInvitationEmailSender",
    "build_invitation_email_sender",
]

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_INVITE_FROM_EMAIL = "Orcheo <invites@orcheo.cloud>"


@dataclass(frozen=True)
class InvitationEmail:
    """Rendered invitation ready to be delivered to a recipient."""

    to: str
    workspace_name: str
    role: str
    accept_url: str
    expires_at: datetime
    invited_by: str | None = None


class InvitationEmailSender(Protocol):
    """Port for delivering workspace invitation emails.

    Production deployments inject an implementation backed by their transactional
    email provider (SES, SendGrid, Resend, SMTP, ...). The send is best effort
    from the caller's perspective: implementations should raise on hard failures
    so the service can surface them.
    """

    def send_invitation(self, email: InvitationEmail) -> None:
        """Deliver a single invitation email."""


class LoggingInvitationEmailSender:
    """Default sender that logs the invitation link instead of sending email.

    Used for local development and self-hosting where no transactional email
    provider is configured. The acceptance URL is logged so operators can copy
    it to the invitee manually.
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


class ResendInvitationEmailSender:
    """Deliver invitation emails through the Resend HTTP API.

    Used in production when ``ORCHEO_RESEND_API_KEY`` is configured. The sender
    address must belong to a domain verified in the Resend dashboard. Raises on
    a non-2xx response so the calling service surfaces delivery failures.
    """

    def __init__(
        self,
        *,
        api_key: str,
        from_email: str = DEFAULT_INVITE_FROM_EMAIL,
        timeout: float = 10.0,
    ) -> None:
        """Bind the sender to a Resend API key and verified ``from`` address."""
        self._api_key = api_key
        self._from_email = from_email
        self._timeout = timeout

    def send_invitation(self, email: InvitationEmail) -> None:
        """POST the rendered invitation to the Resend API."""
        response = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": self._from_email,
                "to": [email.to],
                "subject": (f"You've been invited to {email.workspace_name} on Orcheo"),
                "html": _render_invitation_html(email),
            },
            timeout=self._timeout,
        )
        response.raise_for_status()


def build_invitation_email_sender(
    *,
    api_key: str | None = None,
    from_email: str | None = None,
) -> InvitationEmailSender:
    """Return the configured sender: Resend when an API key is set, else logging.

    Keeping the selection here means deployments opt into real delivery purely
    through configuration, while local/self-hosting setups fall back to logging
    the acceptance link.
    """
    normalized_key = (api_key or "").strip()
    if normalized_key:
        return ResendInvitationEmailSender(
            api_key=normalized_key,
            from_email=(from_email or "").strip() or DEFAULT_INVITE_FROM_EMAIL,
        )
    return LoggingInvitationEmailSender()
