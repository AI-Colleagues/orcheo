"""Shared base models and utilities for Orcheo domain objects."""

from __future__ import annotations
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "_utcnow",
    "AuditRecord",
    "OrcheoBaseModel",
    "TimestampedAuditModel",
]


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC)


class OrcheoBaseModel(BaseModel):
    """Base model that enforces Orcheo validation defaults."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )


class AuditRecord(OrcheoBaseModel):
    """Single audit event describing actor, action, and context."""

    actor: str
    action: str
    at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimestampedAuditModel(OrcheoBaseModel):
    """Base class for entities that track timestamps and audit logs."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    audit_log: list[AuditRecord] = Field(default_factory=list)

    def record_event(
        self,
        *,
        actor: str,
        action: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditRecord:
        """Append an audit entry and update the modification timestamp."""
        entry = AuditRecord(
            actor=actor,
            action=action,
            metadata=dict(metadata or {}),
        )
        self.audit_log.append(entry)
        self.updated_at = entry.at
        return entry
