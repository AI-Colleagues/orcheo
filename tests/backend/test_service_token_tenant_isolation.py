"""Cross-tenant isolation tests for the service token repository."""

from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
import pytest
from orcheo_backend.app.authentication.service_tokens import (
    ServiceTokenManager,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_repository.in_memory_repository import (
    InMemoryServiceTokenRepository,
)
from orcheo_backend.app.service_token_repository.sqlite_repository import (
    SqliteServiceTokenRepository,
)


@pytest.mark.asyncio
async def test_in_memory_list_for_tenant_filters_by_tenant_id() -> None:
    repo = InMemoryServiceTokenRepository()
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-a",
            secret_hash="hash-a",
            tenant_id="tenant-a",
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-b",
            secret_hash="hash-b",
            tenant_id="tenant-b",
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-untagged",
            secret_hash="hash-c",
        )
    )

    a_records = await repo.list_for_tenant("tenant-a")
    b_records = await repo.list_for_tenant("tenant-b")

    assert {r.identifier for r in a_records} == {"tok-a"}
    assert {r.identifier for r in b_records} == {"tok-b"}


@pytest.mark.asyncio
async def test_sqlite_list_for_tenant_filters_by_tenant_id(tmp_path: Path) -> None:
    db = tmp_path / "tokens.db"
    repo = SqliteServiceTokenRepository(db)
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-a",
            secret_hash="hash-a",
            tenant_id="tenant-a",
            issued_at=datetime.now(tz=UTC),
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-b",
            secret_hash="hash-b",
            tenant_id="tenant-b",
            issued_at=datetime.now(tz=UTC),
        )
    )

    a_records = await repo.list_for_tenant("tenant-a")
    b_records = await repo.list_for_tenant("tenant-b")

    assert {r.identifier for r in a_records} == {"tok-a"}
    assert {r.identifier for r in b_records} == {"tok-b"}


@pytest.mark.asyncio
async def test_sqlite_round_trip_preserves_tenant_id(tmp_path: Path) -> None:
    db = tmp_path / "tokens.db"
    repo = SqliteServiceTokenRepository(db)
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-x",
            secret_hash="hash-x",
            tenant_id="tenant-z",
            issued_at=datetime.now(tz=UTC),
        )
    )
    fetched = await repo.find_by_id("tok-x")
    assert fetched is not None
    assert fetched.tenant_id == "tenant-z"


@pytest.mark.asyncio
async def test_service_token_manager_mint_records_tenant_id() -> None:
    repo = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repo)

    secret, record = await manager.mint(tenant_id="tenant-q")
    assert record.tenant_id == "tenant-q"

    # Confirm the persisted record carries the same tenant id.
    persisted = await repo.find_by_id(record.identifier)
    assert persisted is not None
    assert persisted.tenant_id == "tenant-q"
    assert secret  # smoke-check: secret was issued


@pytest.mark.asyncio
async def test_service_token_manager_rotation_preserves_tenant_id() -> None:
    repo = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repo)

    _, record = await manager.mint(tenant_id="tenant-q")
    _, rotated = await manager.rotate(record.identifier)

    assert rotated.tenant_id == "tenant-q"


@pytest.mark.asyncio
async def test_existing_sqlite_db_gets_tenant_id_column(tmp_path: Path) -> None:
    """A pre-existing service_tokens table without tenant_id is migrated additively."""
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE service_tokens (
                identifier TEXT PRIMARY KEY,
                secret_hash TEXT NOT NULL,
                scopes TEXT,
                workspace_ids TEXT,
                created_at TEXT NOT NULL,
                created_by TEXT,
                issued_at TEXT,
                expires_at TEXT,
                last_used_at TEXT,
                use_count INTEGER DEFAULT 0,
                rotation_expires_at TEXT,
                rotated_to TEXT,
                rotated_from TEXT,
                revoked_at TEXT,
                revoked_by TEXT,
                revocation_reason TEXT,
                allowed_ip_ranges TEXT,
                rate_limit_override INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO service_tokens (
                identifier, secret_hash, created_at
            ) VALUES (?, ?, ?)
            """,
            ("old-tok", "old-hash", datetime.now(tz=UTC).isoformat()),
        )

    # Constructing the repository should run the additive migration.
    repo = SqliteServiceTokenRepository(db)
    record = await repo.find_by_id("old-tok")
    assert record is not None
    assert record.tenant_id is None
