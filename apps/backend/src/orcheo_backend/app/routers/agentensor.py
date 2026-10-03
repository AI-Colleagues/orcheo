"""Agentensor checkpoint APIs."""

from __future__ import annotations
import asyncio
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query
from orcheo.agentensor.checkpoints import AgentensorCheckpointNotFoundError
from orcheo.graph.ingestion.sandbox import uploads_allowed
from orcheo_backend.app.dependencies import (
    CheckpointStoreDep,
    RepositoryDep,
    resolve_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_not_found
from orcheo_backend.app.repository import WorkflowVersionNotFoundError
from orcheo_backend.app.schemas.agentensor import (
    AgentensorCheckpointResponse,
    WorkflowEvaluationRequest,
    WorkflowEvaluationResponse,
)
from orcheo_backend.app.workflow_execution import execute_workflow_evaluation
from orcheo_backend.app.workspace import WorkspaceContextDep


router = APIRouter()


class _EvaluationResult:
    """Keep only the final result; the executor persists full event history."""

    def __init__(self) -> None:
        self.result: dict[str, Any] | None = None
        self.status = "running"

    async def send_json(self, data: Any, mode: str = "text") -> None:
        if data.get("event") == "evaluation_result":
            self.result = data.get("payload")
        if "status" in data:
            self.status = data["status"]


@router.post(
    "/workflows/{workflow_ref}/agentensor/evaluate",
    response_model=WorkflowEvaluationResponse,
)
async def evaluate_workflow(
    workflow_ref: str,
    request: WorkflowEvaluationRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowEvaluationResponse:
    """Evaluate a stored version and persist progress without a WebSocket client."""
    tid = str(workspace.workspace_id)
    workflow_id = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=tid
    )
    if request.evaluation.evaluators and not uploads_allowed():
        raise HTTPException(
            status_code=403,
            detail=(
                "Custom evaluator entrypoints require "
                "client code uploads to be enabled."
            ),
        )
    try:
        version = (
            await repository.get_version_by_number(workflow_id, request.version)
            if request.version is not None
            else await repository.get_latest_version(workflow_id)
        )
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)
    execution_id = str(uuid4())
    sink = _EvaluationResult()
    try:
        async with asyncio.timeout(request.timeout_seconds):
            await execute_workflow_evaluation(
                str(workflow_id),
                version.graph,
                request.inputs,
                execution_id,
                sink,
                evaluation=request.evaluation,
                workspace_id=tid,
                runnable_config=request.runnable_config,
                stored_runnable_config=version.runnable_config or {},
            )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "message": f"Evaluation {execution_id} timed out and was cancelled.",
                "execution_id": execution_id,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Evaluation {execution_id} failed: {exc}",
                "execution_id": execution_id,
            },
        ) from exc
    return WorkflowEvaluationResponse(
        execution_id=execution_id,
        status=sink.status,
        result=sink.result,
    )


@router.get(
    "/workflows/{workflow_ref}/agentensor/checkpoints",
    response_model=list[AgentensorCheckpointResponse],
)
async def list_agentensor_checkpoints(
    workflow_ref: str,
    repository: RepositoryDep,
    store: CheckpointStoreDep,
    workspace: WorkspaceContextDep,
    limit: int = Query(20, ge=1, le=200),
) -> list[AgentensorCheckpointResponse]:
    """List checkpoints for the specified workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=str(workspace.workspace_id)
    )
    checkpoints = await store.list_checkpoints(
        str(workflow_uuid), limit=limit, workspace_id=str(workspace.workspace_id)
    )
    return [AgentensorCheckpointResponse.from_domain(item) for item in checkpoints]


@router.get(
    "/workflows/{workflow_ref}/agentensor/checkpoints/{checkpoint_id}",
    response_model=AgentensorCheckpointResponse,
)
async def get_agentensor_checkpoint(
    workflow_ref: str,
    checkpoint_id: str,
    repository: RepositoryDep,
    store: CheckpointStoreDep,
    workspace: WorkspaceContextDep,
) -> AgentensorCheckpointResponse:
    """Return a single checkpoint for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=str(workspace.workspace_id)
    )
    try:
        checkpoint = await store.get_checkpoint(checkpoint_id)
    except AgentensorCheckpointNotFoundError as exc:
        raise_not_found("Checkpoint not found", exc)
    if checkpoint.workflow_id != str(workflow_uuid):
        raise_not_found("Checkpoint not found", AgentensorCheckpointNotFoundError(""))
    return AgentensorCheckpointResponse.from_domain(checkpoint)


__all__ = ["router"]
