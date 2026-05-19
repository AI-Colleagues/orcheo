"""Structured audit logging for sandbox lifecycle and policy events."""

from __future__ import annotations
import logging
from orcheo.sandbox.models import SandboxAuditEvent


class SandboxAuditLogger:
    """Emit sandbox audit events to a named ``logging`` channel.

    Operators can route this channel (default ``orcheo.sandbox.audit``) to a
    durable sink (file, syslog, SIEM) without affecting the rest of the
    application's logging. The logger emits one structured ``INFO`` record per
    event with the event payload exposed via ``logging.extra``.
    """

    def __init__(self, logger_name: str = "orcheo.sandbox.audit") -> None:
        """Initialize the audit logger.

        Args:
            logger_name: Logger name to emit records under.
        """
        self._logger = logging.getLogger(logger_name)

    def emit(self, event: SandboxAuditEvent) -> None:
        """Emit ``event`` as a structured INFO record."""
        self._logger.info(
            "sandbox.audit %s workspace=%s sandbox=%s run=%s detail=%s",
            event.event,
            event.workspace_id,
            event.sandbox_id or "-",
            event.run_id or "-",
            event.detail or "-",
            extra=event.as_log_extra(),
        )
