"""Tests for the sandbox audit logger."""

from __future__ import annotations
import logging
from orcheo.sandbox.audit import SandboxAuditLogger
from orcheo.sandbox.models import SandboxAuditEvent


def test_audit_logger_emits_structured_record(caplog: object) -> None:
    """Each emit call produces one INFO record carrying the event payload."""
    capture = caplog  # type: ignore[assignment]
    audit = SandboxAuditLogger("orcheo.sandbox.audit.test")
    with capture.at_level(logging.INFO, logger="orcheo.sandbox.audit.test"):  # type: ignore[attr-defined]
        audit.emit(
            SandboxAuditEvent(
                event="provision",
                workspace_id="W",
                sandbox_id="S",
                detail="kind=workflow",
            )
        )
    records = [r for r in capture.records if r.name == "orcheo.sandbox.audit.test"]  # type: ignore[attr-defined]
    assert len(records) == 1
    assert records[0].sandbox_event == "provision"  # type: ignore[attr-defined]
    assert records[0].workspace_id == "W"  # type: ignore[attr-defined]
