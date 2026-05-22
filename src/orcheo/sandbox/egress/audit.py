"""Consume Envoy's egress access log and emit sandbox audit events."""

from __future__ import annotations
import json
import logging
from collections.abc import Iterable
from orcheo.sandbox.audit import SandboxAuditLogger
from orcheo.sandbox.models import SandboxAuditEvent


logger = logging.getLogger(__name__)


class EgressAuditConsumer:
    """Translate Envoy access-log JSONL into ``SandboxAuditEvent`` records.

    The proxy is configured to emit one JSON object per request. Denied hosts
    (response code 403 from the proxy itself) become ``egress_denied`` audit
    events; permitted requests are summarized at ``egress_allowed`` but only
    when the sink wants verbose logging.
    """

    def __init__(self, audit: SandboxAuditLogger, *, log_allowed: bool = False) -> None:
        """Initialize the consumer.

        Args:
            audit: Sink for ``SandboxAuditEvent`` records.
            log_allowed: When True, also emit a record for each permitted
                request. Defaults to False so the audit volume stays focused
                on denied traffic.
        """
        self._audit = audit
        self._log_allowed = log_allowed

    def consume(self, lines: Iterable[str]) -> int:
        """Consume newline-delimited proxy log lines.

        Args:
            lines: Iterable of raw log lines. Malformed lines are skipped.

        Returns:
            Count of audit events emitted.
        """
        emitted = 0
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                logger.debug("Skipping malformed proxy log line: %s", line)
                continue
            status = int(payload.get("response_code", 0))
            host = payload.get("host", "")
            workspace_id = payload.get("workspace_id") or payload.get(
                "orcheo_workspace_id", "unknown"
            )
            run_id = payload.get("run_id")
            sandbox_id = payload.get("sandbox_id")
            if status == 403:
                self._audit.emit(
                    SandboxAuditEvent(
                        event="egress_denied",
                        workspace_id=workspace_id,
                        sandbox_id=sandbox_id,
                        run_id=run_id,
                        detail=f"host={host} status={status}",
                    )
                )
                emitted += 1
            elif self._log_allowed:
                self._audit.emit(
                    SandboxAuditEvent(
                        event="egress_allowed",
                        workspace_id=workspace_id,
                        sandbox_id=sandbox_id,
                        run_id=run_id,
                        detail=f"host={host} status={status}",
                    )
                )
                emitted += 1
        return emitted
