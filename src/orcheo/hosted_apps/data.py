"""Bounded app-data documents with server-derived collection and owner scope."""

from __future__ import annotations
import base64
import json
from collections.abc import MutableSequence
from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import UUID, uuid4
from orcheo.hosted_apps.models import AppCollection


__all__ = ["AppDataConflictError", "AppDataService", "AppRecord"]


class AppDataConflictError(ValueError):
    """Raised for optimistic-concurrency failures without disclosing other records."""


@dataclass(frozen=True, slots=True)
class AppRecord:
    """One canonical JSON document scoped by app, stable collection, and owner."""

    id: UUID
    workspace_id: UUID
    app_id: UUID
    collection_id: UUID
    owner_subject: str
    key: str
    value: Any
    size_bytes: int
    version: int


class AppDataService:
    """Reference data service that never accepts client-supplied tenancy fields."""

    def __init__(self, *, max_depth: int = 16, max_keys: int = 256) -> None:
        """Initialize bounded in-memory records for domain and route tests."""
        self._max_depth = max_depth
        self._max_keys = max_keys
        self._records: dict[tuple[UUID, UUID, str, str], AppRecord] = {}
        self._lock = RLock()

    def put(
        self,
        collection: AppCollection,
        *,
        workspace_id: UUID,
        app_id: UUID,
        key: str,
        value: Any,
        subject: str | None,
        expected_version: int | None = None,
    ) -> AppRecord:
        """Create or replace a document after deriving its server-side scope."""
        self._ensure_collection_scope(collection, workspace_id, app_id)
        owner = self._owner(collection, subject, write=True)
        normalized_key = self._normalize_key(key)
        encoded = self._canonical_json(value)
        if len(encoded) > collection.max_document_bytes:
            raise ValueError("App document exceeds this collection's byte limit.")
        record_key = (app_id, collection.id, owner, normalized_key)
        with self._lock:
            current = self._records.get(record_key)
            if expected_version is not None and (
                current is None or current.version != expected_version
            ):
                raise AppDataConflictError("App document version is stale.")
            if current is None:
                count = sum(
                    1
                    for existing in self._records.values()
                    if existing.app_id == app_id
                    and existing.collection_id == collection.id
                    and existing.owner_subject == owner
                )
                if count >= collection.max_records:
                    raise ValueError("App collection record limit has been reached.")
            record = AppRecord(
                id=current.id if current else uuid4(),
                workspace_id=workspace_id,
                app_id=app_id,
                collection_id=collection.id,
                owner_subject=owner,
                key=normalized_key,
                value=value,
                size_bytes=len(encoded),
                version=(current.version + 1) if current else 1,
            )
            self._records[record_key] = record
            return record

    def get(
        self,
        collection: AppCollection,
        *,
        workspace_id: UUID,
        app_id: UUID,
        key: str,
        subject: str | None,
    ) -> AppRecord | None:
        """Read one record only in the collection-derived owner scope."""
        self._ensure_collection_scope(collection, workspace_id, app_id)
        owner = self._owner(collection, subject, write=False)
        with self._lock:
            return self._records.get(
                (app_id, collection.id, owner, self._normalize_key(key))
            )

    def list(
        self,
        collection: AppCollection,
        *,
        workspace_id: UUID,
        app_id: UUID,
        subject: str | None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[AppRecord], str | None]:
        """List owner-scoped records using an opaque key cursor."""
        self._ensure_collection_scope(collection, workspace_id, app_id)
        owner = self._owner(collection, subject, write=False)
        start_after = self._decode_cursor(cursor) if cursor else None
        with self._lock:
            entries = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.app_id == app_id
                    and record.collection_id == collection.id
                    and record.owner_subject == owner
                    and (start_after is None or record.key > start_after)
                ),
                key=lambda record: record.key,
            )
        page = entries[:limit]
        next_cursor = (
            self._encode_cursor(page[-1].key) if len(entries) > limit else None
        )
        return page, next_cursor

    def delete(
        self,
        collection: AppCollection,
        *,
        workspace_id: UUID,
        app_id: UUID,
        key: str,
        subject: str | None,
        expected_version: int | None = None,
    ) -> bool:
        """Delete one owner-scoped key with optional optimistic concurrency."""
        self._ensure_collection_scope(collection, workspace_id, app_id)
        owner = self._owner(collection, subject, write=True)
        record_key = (app_id, collection.id, owner, self._normalize_key(key))
        with self._lock:
            current = self._records.get(record_key)
            if current is None:
                return False
            if expected_version is not None and current.version != expected_version:
                raise AppDataConflictError("App document version is stale.")
            del self._records[record_key]
            return True

    def _ensure_collection_scope(
        self, collection: AppCollection, workspace_id: UUID, app_id: UUID
    ) -> None:
        """Require the stable collection to belong to the resolved app/workspace."""
        if collection.deleted_at is not None:
            raise ValueError("App collection is no longer available.")
        if collection.workspace_id != workspace_id or collection.app_id != app_id:
            raise ValueError("App collection does not belong to this app scope.")

    @staticmethod
    def _normalize_key(value: str) -> str:
        """Normalize bounded record keys without allowing empty or control values."""
        key = value.strip()
        if not key or len(key) > 256 or any(ord(char) < 32 for char in key):
            raise ValueError("App document key is invalid.")
        return key

    def _owner(
        self, collection: AppCollection, subject: str | None, *, write: bool
    ) -> str:
        """Derive user/private ownership and enforce explicit read/write access."""
        access = collection.write_access if write else collection.read_access
        if access == "authenticated" and not subject:
            raise PermissionError("App collection requires an authenticated visitor.")
        if collection.scope == "user":
            if not subject:
                raise PermissionError(
                    "User-scoped app data requires an authenticated visitor."
                )
            return subject
        return ""

    def _canonical_json(self, value: Any) -> bytes:
        """Validate canonical JSON limits before storage or quota accounting."""
        self._walk_json(value, depth=0, key_count=[0])
        try:
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("App document must be JSON serializable.") from exc

    def _walk_json(
        self, value: Any, *, depth: int, key_count: MutableSequence[int]
    ) -> None:
        """Reject excessive nesting and object-key counts before canonical encoding."""
        if depth > self._max_depth:
            raise ValueError("App document exceeds the maximum JSON depth.")
        if isinstance(value, dict):
            key_count[0] += len(value)
            if key_count[0] > self._max_keys:
                raise ValueError("App document exceeds the maximum JSON key count.")
            for nested in value.values():
                self._walk_json(nested, depth=depth + 1, key_count=key_count)
        elif isinstance(value, list):
            for nested in value:
                self._walk_json(nested, depth=depth + 1, key_count=key_count)

    @staticmethod
    def _encode_cursor(key: str) -> str:
        """Encode a stable cursor without exposing internal record identifiers."""
        return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> str:
        """Decode and validate an opaque list cursor."""
        try:
            padding = "=" * (-len(cursor) % 4)
            return base64.urlsafe_b64decode(cursor + padding).decode()
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("App data cursor is invalid.") from exc
