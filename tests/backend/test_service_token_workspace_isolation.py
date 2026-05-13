"""Cross-workspace isolation tests for the service token repository."""

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
async def test_in_memory_list_for_workspace_filters_by_workspace_id() -> None:
    repo = InMemoryServiceTokenRepository()
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-a",
            secret_hash="hash-a",
            workspace_id="workspace-a",
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-b",
            secret_hash="hash-b",
            workspace_id="workspace-b",
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-untagged",
            secret_hash="hash-c",
        )
    )

    a_records = await repo.list_for_workspace("workspace-a")
    b_records = await repo.list_for_workspace("workspace-b")

    assert {r.identifier for r in a_records} == {"tok-a"}
    assert {r.identifier for r in b_records} == {"tok-b"}


@pytest.mark.asyncio
async def test_sqlite_list_for_workspace_filters_by_workspace_id(
    tmp_path: Path,
) -> None:
    db = tmp_path / "tokens.db"
    repo = SqliteServiceTokenRepository(db)
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-a",
            secret_hash="hash-a",
            workspace_id="workspace-a",
            issued_at=datetime.now(tz=UTC),
        )
    )
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-b",
            secret_hash="hash-b",
            workspace_id="workspace-b",
            issued_at=datetime.now(tz=UTC),
        )
    )

    a_records = await repo.list_for_workspace("workspace-a")
    b_records = await repo.list_for_workspace("workspace-b")

    assert {r.identifier for r in a_records} == {"tok-a"}
    assert {r.identifier for r in b_records} == {"tok-b"}


@pytest.mark.asyncio
async def test_sqlite_round_trip_preserves_workspace_id(tmp_path: Path) -> None:
    db = tmp_path / "tokens.db"
    repo = SqliteServiceTokenRepository(db)
    await repo.create(
        ServiceTokenRecord(
            identifier="tok-x",
            secret_hash="hash-x",
            workspace_id="workspace-z",
            issued_at=datetime.now(tz=UTC),
        )
    )
    fetched = await repo.find_by_id("tok-x")
    assert fetched is not None
    assert fetched.workspace_id == "workspace-z"


@pytest.mark.asyncio
async def test_service_token_manager_mint_records_workspace_id() -> None:
    repo = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repo)

    secret, record = await manager.mint(workspace_id="workspace-q")
    assert record.workspace_id == "workspace-q"

    # Confirm the persisted record carries the same workspace id.
    persisted = await repo.find_by_id(record.identifier)
    assert persisted is not None
    assert persisted.workspace_id == "workspace-q"
    assert secret  # smoke-check: secret was issued


@pytest.mark.asyncio
async def test_service_token_manager_rotation_preserves_workspace_id() -> None:
    repo = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repo)

    _, record = await manager.mint(workspace_id="workspace-q")
    _, rotated = await manager.rotate(record.identifier)

    assert rotated.workspace_id == "workspace-q"


@pytest.mark.asyncio
async def test_existing_sqlite_db_gets_workspace_id_column(tmp_path: Path) -> None:
    """A pre-existing service_tokens table without workspace_id is migrated additively."""
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
    assert record.workspace_id is None
