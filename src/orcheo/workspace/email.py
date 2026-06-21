"""Invitation email delivery port and a default logging implementation."""

from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


__all__ = [
    "InvitationEmail",
    "InvitationEmailSender",
    "LoggingInvitationEmailSender",
]

logger = logging.getLogger(__name__)


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
