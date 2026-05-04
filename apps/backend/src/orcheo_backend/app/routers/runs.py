"""Workflow run management routes."""

from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, status
from orcheo.models.workflow import WorkflowRun
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.dependencies import (
    CredentialServiceDep,
    HistoryStoreDep,
    RepositoryDep,
    resolve_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_conflict, raise_not_found
from orcheo_backend.app.history import RunHistoryNotFoundError
from orcheo_backend.app.history_utils import history_to_response
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.runs import (
    RunActionRequest,
    RunCancelRequest,
    RunFailRequest,
    RunHistoryResponse,
    RunReplayRequest,
    RunSucceedRequest,
)
from orcheo_backend.app.schemas.traces import TraceResponse
from orcheo_backend.app.schemas.workflows import WorkflowRunCreateRequest
from orcheo_backend.app.tenancy import TenantContextDep
from orcheo_backend.app.trace_utils import build_trace_response


router = APIRouter()


@router.post(
    "/workflows/{workflow_ref}/runs",
    response_model=WorkflowRun,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_run(
    workflow_ref: str,
    request: WorkflowRunCreateRequest,
    repository: RepositoryDep,
    tenant: TenantContextDep,
    _service: CredentialServiceDep,
) -> WorkflowRun:
    """Create a workflow execution run."""
    tid = str(tenant.tenant_id)
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=tid
    )
    try:
        config_payload = (
            request.runnable_config.model_dump(mode="json")
            if request.runnable_config is not None
            else None
        )
        return await repository.create_run(
            workflow_uuid,
            workflow_version_id=request.workflow_version_id,
            triggered_by=request.triggered_by,
            input_payload=request.input_payload,
            runnable_config=config_payload,
            tenant_id=tid,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "failures": exc.report.failures},
        ) from exc


_DEFAULT_RUNS_LIMIT = 50


@router.get(
    "/workflows/{workflow_ref}/runs",
    response_model=list[WorkflowRun],
)
async def list_workflow_runs(
    workflow_ref: str,
    repository: RepositoryDep,
    tenant: TenantContextDep,
    limit: int = Query(_DEFAULT_RUNS_LIMIT, ge=1, le=200),
) -> list[WorkflowRun]:
    """List runs for a given workflow (most recent first)."""
    tid = str(tenant.tenant_id)
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=tid
    )
    try:
        return await repository.list_runs_for_workflow(
            workflow_uuid, limit=limit, tenant_id=tid
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


@router.get("/runs/{run_id}", response_model=WorkflowRun)
async def get_workflow_run(
    run_id: UUID,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> WorkflowRun:
    """Retrieve a single workflow run."""
    try:
        return await repository.get_run(run_id, tenant_id=str(tenant.tenant_id))
    except WorkflowRunNotFoundError as exc:
        raise_not_found("Workflow run not found", exc)


@router.post("/runs/{run_id}/start", response_model=WorkflowRun)
async def mark_run_started(
    run_id: UUID,
    request: RunActionRequest,
    repository: RepositoryDep,
) -> WorkflowRun:
    """Transition a run into the running state."""
    try:
        return await repository.mark_run_started(run_id, actor=request.actor)
    except WorkflowRunNotFoundError as exc:
        raise_not_found("Workflow run not found", exc)
    except ValueError as exc:
        raise_conflict(str(exc), exc)


@router.post("/runs/{run_id}/succeed", response_model=WorkflowRun)
async def mark_run_succeeded(
    run_id: UUID,
    request: RunSucceedRequest,
    repository: RepositoryDep,
) -> WorkflowRun:
    """Mark a workflow run as successful."""
    try:
        return await repository.mark_run_succeeded(
            run_id,
            actor=request.actor,
            output=request.output,
        )
    except WorkflowRunNotFoundError as exc:
        raise_not_found("Workflow run not found", exc)
    except ValueError as exc:
        raise_conflict(str(exc), exc)


@router.post("/runs/{run_id}/fail", response_model=WorkflowRun)
async def mark_run_failed(
    run_id: UUID,
    request: RunFailRequest,
    repository: RepositoryDep,
) -> WorkflowRun:
    """Mark a workflow run as failed."""
    try:
        return await repository.mark_run_failed(
            run_id,
            actor=request.actor,
            error=request.error,
        )
    except WorkflowRunNotFoundError as exc:
        raise_not_found("Workflow run not found", exc)
    except ValueError as exc:
        raise_conflict(str(exc), exc)


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRun)
async def mark_run_cancelled(
    run_id: UUID,
    request: RunCancelRequest,
    repository: RepositoryDep,
) -> WorkflowRun:
    """Cancel a workflow run."""
    try:
        return await repository.mark_run_cancelled(
            run_id,
            actor=request.actor,
            reason=request.reason,
        )
    except WorkflowRunNotFoundError as exc:
        raise_not_found("Workflow run not found", exc)
    except ValueError as exc:
        raise_conflict(str(exc), exc)


@router.get(
    "/workflows/{workflow_ref}/executions",
    response_model=list[RunHistoryResponse],
)
async def list_workflow_execution_histories(
    workflow_ref: str,
    history_store: HistoryStoreDep,
    repository: RepositoryDep,
    tenant: TenantContextDep,
    limit: int = Query(50, ge=1, le=200),
) -> list[RunHistoryResponse]:
    """Return execution histories recorded for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    records = await history_store.list_histories(
        str(workflow_uuid), limit=limit, tenant_id=str(tenant.tenant_id)
    )
    return [history_to_response(record) for record in records]


@router.get(
    "/executions/{execution_id}/history",
    response_model=RunHistoryResponse,
)
async def get_execution_history(
    execution_id: str,
    history_store: HistoryStoreDep,
) -> RunHistoryResponse:
    """Return the recorded execution history for a workflow run."""
    try:
        record = await history_store.get_history(execution_id)
    except RunHistoryNotFoundError as exc:
        raise_not_found("Execution history not found", exc)
    return history_to_response(record)


@router.get(
    "/executions/{execution_id}/trace",
    response_model=TraceResponse,
)
async def get_execution_trace(
    execution_id: str,
    history_store: HistoryStoreDep,
) -> TraceResponse:
    """Return the trace metadata assembled from execution history."""
    try:
        record = await history_store.get_history(execution_id)
    except RunHistoryNotFoundError as exc:
        raise_not_found("Execution history not found", exc)
    return build_trace_response(record)


@router.post(
    "/executions/{execution_id}/replay",
    response_model=RunHistoryResponse,
)
async def replay_execution(
    execution_id: str,
    request: RunReplayRequest,
    history_store: HistoryStoreDep,
) -> RunHistoryResponse:
    """Return a sliced view of the execution history for replay clients."""
    try:
        record = await history_store.get_history(execution_id)
    except RunHistoryNotFoundError as exc:
        raise_not_found("Execution history not found", exc)
    return history_to_response(record, from_step=request.from_step)


__all__ = ["router"]
