"""Serialization helpers for the SQLite service token repository."""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from orcheo_backend.app.authentication import ServiceTokenRecord


def serialize_string_set(values: frozenset[str] | None) -> str | None:
    """Serialize a frozenset of strings to stable JSON or None when empty."""
    if not values:
        return None
    return json.dumps(sorted(values))


def serialize_datetime(value: datetime | None) -> str | None:
    """Serialize an optional datetime to ISO format."""
    return value.isoformat() if value else None


def row_to_record(row: sqlite3.Row) -> ServiceTokenRecord:
    """Convert a sqlite3.Row into a ServiceTokenRecord."""
    keys = row.keys() if hasattr(row, "keys") else ()
    tenant_id = row["tenant_id"] if "tenant_id" in keys else None
    return ServiceTokenRecord(
        identifier=row["identifier"],
        secret_hash=row["secret_hash"],
        scopes=_deserialize_string_set(row["scopes"]),
        workspace_ids=_deserialize_string_set(row["workspace_ids"]),
        issued_at=_parse_timestamp(row["issued_at"]),
        expires_at=_parse_timestamp(row["expires_at"]),
        rotation_expires_at=_parse_timestamp(row["rotation_expires_at"]),
        revoked_at=_parse_timestamp(row["revoked_at"]),
        revocation_reason=row["revocation_reason"],
        rotated_to=row["rotated_to"],
        last_used_at=_parse_timestamp(row["last_used_at"]),
        use_count=int(row["use_count"]) if row["use_count"] is not None else 0,
        tenant_id=tenant_id,
    )


def _deserialize_string_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(json.loads(value))


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
