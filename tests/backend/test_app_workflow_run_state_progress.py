"""Tests for workflow run transitions to running and succeeded states."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import WorkflowRun
from orcheo_backend.app import mark_run_started, mark_run_succeeded
from orcheo_backend.app.repository import WorkflowRunNotFoundError
from orcheo_backend.app.schemas.runs import RunActionRequest, RunSucceedRequest


@pytest.mark.asyncio()
async def test_mark_run_started_success() -> None:
    """Mark run started endpoint transitions run to running state."""

    run_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def mark_run_started(self, run_id, actor):
            return WorkflowRun(
                id=run_id,
                workflow_version_id=version_id,
                triggered_by="user",
                input_payload={},
                status="running",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = RunActionRequest(actor="system")
    result = await mark_run_started(run_id, request, Repository())

    assert result.id == run_id
    assert result.status == "running"


@pytest.mark.asyncio()
async def test_mark_run_started_not_found() -> None:
    """Mark run started raises 404 for missing run."""

    run_id = uuid4()

    class Repository:
        async def mark_run_started(self, run_id, actor):
            raise WorkflowRunNotFoundError("not found")

    request = RunActionRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_started(run_id, request, Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_mark_run_started_conflict() -> None:
    """Mark run started raises 409 for invalid state transition."""

    run_id = uuid4()

    class Repository:
        async def mark_run_started(self, run_id, actor):
            raise ValueError("Invalid state transition")

    request = RunActionRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_started(run_id, request, Repository())

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio()
async def test_mark_run_succeeded_success() -> None:
    """Mark run succeeded endpoint marks run as successful."""

    run_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def mark_run_succeeded(self, run_id, actor, output):
            return WorkflowRun(
                id=run_id,
                workflow_version_id=version_id,
                triggered_by="user",
                input_payload={},
                output_payload=output,
                status="succeeded",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = RunSucceedRequest(actor="system", output={"result": "ok"})
    result = await mark_run_succeeded(run_id, request, Repository())

    assert result.id == run_id
    assert result.status == "succeeded"


@pytest.mark.asyncio()
async def test_mark_run_succeeded_not_found() -> None:
    """Mark run succeeded raises 404 for missing run."""

    run_id = uuid4()

    class Repository:
        async def mark_run_succeeded(self, run_id, actor, output):
            raise WorkflowRunNotFoundError("not found")

    request = RunSucceedRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_succeeded(run_id, request, Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_mark_run_succeeded_conflict() -> None:
    """Mark run succeeded raises 409 for invalid state transition."""

    run_id = uuid4()

    class Repository:
        async def mark_run_succeeded(self, run_id, actor, output):
            raise ValueError("Invalid state transition")

    request = RunSucceedRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_succeeded(run_id, request, Repository())

    assert exc_info.value.status_code == 409
