"""Isolation, limits, and optimistic concurrency tests for managed app data."""

from __future__ import annotations

from uuid import uuid4
import pytest
from orcheo.hosted_apps import AppCollection, AppDataConflictError, AppDataService


def _collection(*, scope: str = "user") -> AppCollection:
    """Build a declared collection owned by one app/workspace pair."""
    return AppCollection(
        workspace_id=uuid4(),
        app_id=uuid4(),
        name="preferences",
        scope=scope,
        read_access="authenticated" if scope == "user" else "anonymous",
        write_access="authenticated" if scope == "user" else "anonymous",
        max_document_bytes=1000,
        max_records=2,
    )


def test_user_scope_is_derived_and_cannot_cross_read() -> None:
    """A caller cannot supply another user's data scope through document fields."""
    collection = _collection()
    service = AppDataService()
    record = service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="theme",
        value={"mode": "dark", "owner": "not-authoritative"},
        subject="user-a",
    )
    assert record.version == 1
    assert (
        service.get(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="theme",
            subject="user-b",
        )
        is None
    )


def test_data_update_uses_optimistic_version_and_opaque_cursor() -> None:
    """Concurrent updates are detected and pagination exposes no record UUIDs."""
    collection = _collection(scope="shared")
    service = AppDataService()
    first = service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="a",
        value={"value": 1},
        subject=None,
    )
    service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="b",
        value={"value": 2},
        subject=None,
    )
    with pytest.raises(AppDataConflictError):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="a",
            value={"value": 3},
            subject=None,
            expected_version=first.version + 1,
        )
    page, cursor = service.list(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        subject=None,
        limit=1,
    )
    assert [record.key for record in page] == ["a"]
    assert cursor is not None and str(first.id) not in cursor
