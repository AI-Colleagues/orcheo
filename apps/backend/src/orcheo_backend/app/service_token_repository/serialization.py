"""Shared serialization helpers for service token repositories."""

from __future__ import annotations
import json
from datetime import datetime


def serialize_string_set(values: frozenset[str] | None) -> str | None:
    """Serialize a frozenset of strings to stable JSON or None when empty."""
    if not values:
        return None
    return json.dumps(sorted(values))


def serialize_datetime(value: datetime | None) -> str | None:
    """Serialize an optional datetime to ISO format."""
    return value.isoformat() if value else None
