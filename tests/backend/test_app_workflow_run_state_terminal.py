"""Tests for workflow run transitions to failed and cancelled states."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import WorkflowRun
from orcheo_backend.app import mark_run_cancelled, mark_run_failed
from orcheo_backend.app.repository import WorkflowRunNotFoundError
from orcheo_backend.app.schemas.runs import RunCancelRequest, RunFailRequest


@pytest.mark.asyncio()
async def test_mark_run_failed_success() -> None:
    """Mark run failed endpoint records failure details."""

    run_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def mark_run_failed(self, run_id, actor, error):
            return WorkflowRun(
                id=run_id,
                workflow_version_id=version_id,
                triggered_by="user",
                input_payload={},
                error=error,
                status="failed",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = RunFailRequest(actor="system", error="Test error")
    result = await mark_run_failed(run_id, request, Repository())

    assert result.id == run_id
    assert result.status == "failed"


@pytest.mark.asyncio()
async def test_mark_run_failed_not_found() -> None:
    """Mark run failed raises 404 for missing run."""

    run_id = uuid4()

    class Repository:
        async def mark_run_failed(self, run_id, actor, error):
            raise WorkflowRunNotFoundError("not found")

    request = RunFailRequest(actor="system", error="Test error")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_failed(run_id, request, Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_mark_run_failed_conflict() -> None:
    """Mark run failed raises 409 for invalid state transition."""

    run_id = uuid4()

    class Repository:
        async def mark_run_failed(self, run_id, actor, error):
            raise ValueError("Invalid state transition")

    request = RunFailRequest(actor="system", error="Test error")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_failed(run_id, request, Repository())

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio()
async def test_mark_run_cancelled_success() -> None:
    """Mark run cancelled endpoint cancels run."""

    run_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def mark_run_cancelled(self, run_id, actor, reason):
            return WorkflowRun(
                id=run_id,
                workflow_version_id=version_id,
                triggered_by="user",
                input_payload={},
                status="cancelled",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = RunCancelRequest(actor="system", reason="User requested")
    result = await mark_run_cancelled(run_id, request, Repository())

    assert result.id == run_id
    assert result.status == "cancelled"


@pytest.mark.asyncio()
async def test_mark_run_cancelled_not_found() -> None:
    """Mark run cancelled raises 404 for missing run."""

    run_id = uuid4()

    class Repository:
        async def mark_run_cancelled(self, run_id, actor, reason):
            raise WorkflowRunNotFoundError("not found")

    request = RunCancelRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_cancelled(run_id, request, Repository())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_mark_run_cancelled_conflict() -> None:
    """Mark run cancelled raises 409 for invalid state transition."""

    run_id = uuid4()

    class Repository:
        async def mark_run_cancelled(self, run_id, actor, reason):
            raise ValueError("Invalid state transition")

    request = RunCancelRequest(actor="system")

    with pytest.raises(HTTPException) as exc_info:
        await mark_run_cancelled(run_id, request, Repository())

    assert exc_info.value.status_code == 409
