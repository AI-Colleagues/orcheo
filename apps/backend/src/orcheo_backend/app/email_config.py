"""Shared transactional email configuration for the backend.

Builds the SMTP-backed transactional email sender from settings. SMTP is the
sole production transport for both workspace invitations and first-party auth
challenges; when no SMTP host is configured the logging sender is used (the
self-host/dev default).
"""

from __future__ import annotations
from orcheo.config import get_settings
from orcheo.workspace.email import (
    DEFAULT_INVITE_FROM_EMAIL,
    SmtpSettings,
    TransactionalEmailSender,
    build_email_sender,
)


__all__ = ["build_smtp_settings", "build_transactional_email_sender"]


def _parse_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def build_smtp_settings() -> SmtpSettings | None:
    """Return SMTP settings from the environment, or None when unconfigured."""
    settings = get_settings()
    host = settings.get("SMTP_HOST")
    if not host:
        return None
    return SmtpSettings(
        host=str(host),
        port=int(settings.get("SMTP_PORT") or 587),
        username=settings.get("SMTP_USERNAME"),
        password=settings.get("SMTP_PASSWORD"),
        from_email=str(
            settings.get("SMTP_FROM_EMAIL")
            or settings.get("INVITE_FROM_EMAIL")
            or DEFAULT_INVITE_FROM_EMAIL
        ),
        use_tls=_parse_bool(settings.get("SMTP_USE_TLS", True), True),
    )


def build_transactional_email_sender() -> TransactionalEmailSender:
    """Return the configured transactional email sender (SMTP or logging)."""
    return build_email_sender(smtp=build_smtp_settings())
