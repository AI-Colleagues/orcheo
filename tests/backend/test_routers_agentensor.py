"""Tests for the Agentensor checkpoint routers."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.agentensor.checkpoints import (
    AgentensorCheckpoint,
    AgentensorCheckpointNotFoundError,
)
from orcheo_backend.app.routers.agentensor import (
    get_agentensor_checkpoint,
    list_agentensor_checkpoints,
)


_MOCK_TENANT = SimpleNamespace(workspace_id=uuid4())


class DummyStore:
    def __init__(self, checkpoint: AgentensorCheckpoint) -> None:
        self.checkpoint = checkpoint

    async def list_checkpoints(
        self, workflow_id: str, *, limit: int, workspace_id: str | None = None
    ) -> list[AgentensorCheckpoint]:
        return [self.checkpoint]

    async def get_checkpoint(self, checkpoint_id: str) -> AgentensorCheckpoint:
        if self.checkpoint.id != checkpoint_id:
            raise AgentensorCheckpointNotFoundError("missing")
        return self.checkpoint


def _build_checkpoint(workflow_id: str) -> AgentensorCheckpoint:
    return AgentensorCheckpoint(
        workflow_id=workflow_id,
        config_version=1,
        runnable_config={"alpha": "beta"},
        metrics={"score": 1.0},
        metadata={"stage": "test"},
        artifact_url="s3://bucket/checkpoint",
        is_best=True,
    )


class _Repository:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))


@pytest.mark.asyncio
async def test_list_agentensor_checkpoints_returns_payloads() -> None:
    workflow_uuid = uuid4()
    checkpoint = _build_checkpoint(str(workflow_uuid))
    store = DummyStore(checkpoint)

    response = await list_agentensor_checkpoints(
        workflow_ref=str(workflow_uuid),
        repository=_Repository(),
        store=store,  # type: ignore[arg-type]
        workspace=_MOCK_TENANT,
        limit=1,
    )

    assert len(response) == 1
    assert response[0].id == checkpoint.id
    assert response[0].workflow_id == str(workflow_uuid)


@pytest.mark.asyncio
async def test_get_agentensor_checkpoint_raises_not_found() -> None:
    workflow_id = uuid4()
    checkpoint = _build_checkpoint(str(workflow_id))
    store = DummyStore(checkpoint)

    with pytest.raises(HTTPException) as exc_info:
        await get_agentensor_checkpoint(
            workflow_ref=str(uuid4()),  # mismatch on purpose
            checkpoint_id=uuid4().hex,
            repository=_Repository(),
            store=store,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_agentensor_checkpoint_requires_matching_workflow() -> None:
    workflow_id = uuid4()
    checkpoint = _build_checkpoint(str(workflow_id))
    store = DummyStore(checkpoint)

    with pytest.raises(HTTPException) as exc_info:
        await get_agentensor_checkpoint(
            workflow_ref=str(uuid4()),
            checkpoint_id=checkpoint.id,
            repository=_Repository(),
            store=store,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_agentensor_checkpoint_returns_record() -> None:
    workflow_id = uuid4()
    checkpoint = _build_checkpoint(str(workflow_id))
    store = DummyStore(checkpoint)

    response = await get_agentensor_checkpoint(
        workflow_ref=str(workflow_id),
        checkpoint_id=checkpoint.id,
        repository=_Repository(),
        store=store,  # type: ignore[arg-type]
    )

    assert response.id == checkpoint.id
    assert response.workflow_id == str(workflow_id)
