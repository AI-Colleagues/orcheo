"""Cross-workspace isolation tests for the service token repository."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication.service_tokens import (
    ServiceTokenManager,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_repository.in_memory_repository import (
    InMemoryServiceTokenRepository,
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
