"""Governance alerts for managed credentials."""

from __future__ import annotations
from collections.abc import MutableMapping
from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import Field
from orcheo.models.base import TimestampedAuditModel, _utcnow
from orcheo.models.credential_scope import CredentialScope


__all__ = [
    "GovernanceAlertKind",
    "SecretGovernanceAlert",
    "SecretGovernanceAlertSeverity",
]


class GovernanceAlertKind(str, Enum):
    """Kinds of governance alerts tracked by the vault."""

    TOKEN_EXPIRING = "token_expiring"
    VALIDATION_FAILED = "validation_failed"
    ROTATION_OVERDUE = "rotation_overdue"


class SecretGovernanceAlertSeverity(str, Enum):
    """Severity levels assigned to governance alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SecretGovernanceAlert(TimestampedAuditModel):
    """Persisted alert describing governance issues for secrets."""

    workspace_id: str | None = None
    scope: CredentialScope = Field(default_factory=CredentialScope.unrestricted)
    template_id: UUID | None = None
    credential_id: UUID | None = None
    kind: GovernanceAlertKind
    severity: SecretGovernanceAlertSeverity
    message: str
    is_acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None

    @classmethod
    def create(
        cls,
        *,
        scope: CredentialScope,
        kind: GovernanceAlertKind,
        severity: SecretGovernanceAlertSeverity,
        message: str,
        actor: str,
        template_id: UUID | None = None,
        credential_id: UUID | None = None,
        workspace_id: str | None = None,
    ) -> SecretGovernanceAlert:
        """Instantiate a new governance alert with an audit entry."""
        alert = cls(
            workspace_id=workspace_id,
            scope=scope,
            template_id=template_id,
            credential_id=credential_id,
            kind=kind,
            severity=severity,
            message=message,
        )
        alert.record_event(
            actor=actor,
            action="alert_created",
            metadata={"kind": kind.value, "severity": severity.value},
        )
        return alert

    def acknowledge(self, *, actor: str) -> None:
        """Mark the alert as acknowledged by the provided actor."""
        if self.is_acknowledged:
            return
        self.is_acknowledged = True
        self.acknowledged_at = _utcnow()
        self.acknowledged_by = actor
        self.record_event(actor=actor, action="alert_acknowledged")

    def redact(self) -> MutableMapping[str, object]:
        """Return a serialisable representation without sensitive context."""
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "severity": self.severity.value,
            "message": self.message,
            "template_id": str(self.template_id) if self.template_id else None,
            "credential_id": str(self.credential_id) if self.credential_id else None,
            "is_acknowledged": self.is_acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat()
            if self.acknowledged_at
            else None,
        }
