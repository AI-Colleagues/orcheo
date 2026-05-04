"""Agentensor checkpoint APIs."""

from __future__ import annotations
from fastapi import APIRouter, Query
from orcheo.agentensor.checkpoints import AgentensorCheckpointNotFoundError
from orcheo_backend.app.dependencies import (
    CheckpointStoreDep,
    RepositoryDep,
    resolve_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_not_found
from orcheo_backend.app.schemas.agentensor import AgentensorCheckpointResponse
from orcheo_backend.app.tenancy import TenantContextDep


router = APIRouter()


@router.get(
    "/workflows/{workflow_ref}/agentensor/checkpoints",
    response_model=list[AgentensorCheckpointResponse],
)
async def list_agentensor_checkpoints(
    workflow_ref: str,
    repository: RepositoryDep,
    store: CheckpointStoreDep,
    tenant: TenantContextDep,
    limit: int = Query(20, ge=1, le=200),
) -> list[AgentensorCheckpointResponse]:
    """List checkpoints for the specified workflow."""
    workflow_uuid = await resolve_workflow_ref_id(repository, workflow_ref)
    checkpoints = await store.list_checkpoints(
        str(workflow_uuid), limit=limit, tenant_id=str(tenant.tenant_id)
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
) -> AgentensorCheckpointResponse:
    """Return a single checkpoint for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(repository, workflow_ref)
    try:
        checkpoint = await store.get_checkpoint(checkpoint_id)
    except AgentensorCheckpointNotFoundError as exc:
        raise_not_found("Checkpoint not found", exc)
    if checkpoint.workflow_id != str(workflow_uuid):
        raise_not_found("Checkpoint not found", AgentensorCheckpointNotFoundError(""))
    return AgentensorCheckpointResponse.from_domain(checkpoint)


__all__ = ["router"]
