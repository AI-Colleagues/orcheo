"""Tests for workflow execution history endpoints."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo_backend.app import (
    list_workflow_execution_histories,
)
from orcheo_backend.app.history import RunHistoryNotFoundError, RunHistoryRecord
from orcheo_backend.app.schemas.runs import RunReplayRequest


_MOCK_TENANT = SimpleNamespace(tenant_id=uuid4())


class _Repository:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))


@pytest.mark.asyncio()
async def test_list_workflow_execution_histories_returns_records() -> None:
    """The execution history endpoint returns a list of history responses."""
    workflow_id = uuid4()
    execution_id_1 = str(uuid4())
    execution_id_2 = str(uuid4())

    class HistoryStore:
        async def list_histories(
            self, workflow_id: str, *, limit: int, tenant_id: str | None = None
        ):
            return [
                RunHistoryRecord(
                    workflow_id=workflow_id,
                    execution_id=execution_id_1,
                    inputs={"param": "value1"},
                ),
                RunHistoryRecord(
                    workflow_id=workflow_id,
                    execution_id=execution_id_2,
                    inputs={"param": "value2"},
                ),
            ]

    response = await list_workflow_execution_histories(
        workflow_ref=str(workflow_id),
        history_store=HistoryStore(),
        repository=_Repository(),
        tenant=_MOCK_TENANT,
        limit=50,
    )

    assert len(response) == 2
    assert response[0].execution_id == execution_id_1
    assert response[1].execution_id == execution_id_2
    assert response[0].inputs == {"param": "value1"}
    assert response[1].inputs == {"param": "value2"}


@pytest.mark.asyncio()
async def test_list_workflow_execution_histories_respects_limit() -> None:
    """The execution history endpoint passes limit to the store."""
    workflow_id = uuid4()
    limit_value = None

    class HistoryStore:
        async def list_histories(
            self, workflow_id: str, *, limit: int, tenant_id: str | None = None
        ):
            nonlocal limit_value
            limit_value = limit
            return []

    await list_workflow_execution_histories(
        workflow_ref=str(workflow_id),
        history_store=HistoryStore(),
        repository=_Repository(),
        tenant=_MOCK_TENANT,
        limit=100,
    )

    assert limit_value == 100


@pytest.mark.asyncio()
async def test_get_execution_history_success() -> None:
    """Get execution history endpoint returns history."""
    from orcheo_backend.app import get_execution_history

    execution_id = "test-exec-123"

    class HistoryStore:
        async def get_history(self, exec_id):
            return RunHistoryRecord(
                workflow_id=str(uuid4()),
                execution_id=exec_id,
                inputs={"test": "data"},
            )

    result = await get_execution_history(execution_id, HistoryStore())

    assert result.execution_id == execution_id


@pytest.mark.asyncio()
async def test_get_execution_history_not_found() -> None:
    """Get execution history raises 404 for missing execution."""
    from orcheo_backend.app import get_execution_history

    execution_id = "missing-exec"

    class HistoryStore:
        async def get_history(self, exec_id):
            raise RunHistoryNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_execution_history(execution_id, HistoryStore())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_replay_execution_success() -> None:
    """Replay execution endpoint returns sliced history."""
    from orcheo_backend.app import replay_execution
    from orcheo_backend.app.history import RunHistoryStep

    execution_id = "test-exec-123"

    class HistoryStore:
        async def get_history(self, exec_id):
            record = RunHistoryRecord(
                workflow_id=str(uuid4()),
                execution_id=exec_id,
                inputs={"test": "data"},
            )
            record.steps = [
                RunHistoryStep(index=0, payload={"step": 1}),
                RunHistoryStep(index=1, payload={"step": 2}),
                RunHistoryStep(index=2, payload={"step": 3}),
            ]
            return record

    request = RunReplayRequest(from_step=1)
    result = await replay_execution(execution_id, request, HistoryStore())

    assert result.execution_id == execution_id
    assert len(result.steps) == 2
    assert result.steps[0].index == 1


@pytest.mark.asyncio()
async def test_replay_execution_not_found() -> None:
    """Replay execution raises 404 for missing execution."""
    from orcheo_backend.app import replay_execution

    execution_id = "missing-exec"

    class HistoryStore:
        async def get_history(self, exec_id):
            raise RunHistoryNotFoundError("not found")

    request = RunReplayRequest(from_step=0)

    with pytest.raises(HTTPException) as exc_info:
        await replay_execution(execution_id, request, HistoryStore())

    assert exc_info.value.status_code == 404
