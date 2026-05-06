"""Workflow remediation API routes."""

from __future__ import annotations
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from orcheo.models.workflow import WorkflowRunRemediation, WorkflowRunRemediationStatus
from orcheo_backend.app.dependencies import RepositoryDep
from orcheo_backend.app.errors import raise_conflict, raise_not_found
from orcheo_backend.app.repository import WorkflowRunRemediationNotFoundError


router = APIRouter()

WorkflowIdQuery = Annotated[UUID | None, Query()]
WorkflowVersionIdQuery = Annotated[UUID | None, Query()]
RunIdQuery = Annotated[UUID | None, Query()]
StatusQuery = Annotated[WorkflowRunRemediationStatus | None, Query()]
LimitQuery = Annotated[int | None, Query(ge=1, le=200)]


class RemediationDismissRequest(BaseModel):
    """Payload for dismissing a remediation candidate."""

    actor: str = Field(default="system", min_length=1, max_length=255)
    reason: str | None = None


@router.get(
    "/workflow-remediations",
    response_model=list[WorkflowRunRemediation],
)
async def list_workflow_remediations(
    repository: RepositoryDep,
    workflow_id: WorkflowIdQuery = None,
    workflow_version_id: WorkflowVersionIdQuery = None,
    run_id: RunIdQuery = None,
    status: StatusQuery = None,
    limit: LimitQuery = None,
) -> list[WorkflowRunRemediation]:
    """List workflow remediation candidates."""
    return await repository.list_remediation_candidates(
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
        run_id=run_id,
        status=status,
        limit=limit,
    )


@router.get(
    "/workflow-remediations/{remediation_id}",
    response_model=WorkflowRunRemediation,
)
async def get_workflow_remediation(
    remediation_id: UUID,
    repository: RepositoryDep,
) -> WorkflowRunRemediation:
    """Return one workflow remediation candidate."""
    try:
        return await repository.get_remediation_candidate(remediation_id)
    except WorkflowRunRemediationNotFoundError as exc:
        raise_not_found("Workflow remediation not found", exc)


@router.post(
    "/workflow-remediations/{remediation_id}/dismiss",
    response_model=WorkflowRunRemediation,
)
async def dismiss_workflow_remediation(
    remediation_id: UUID,
    request: RemediationDismissRequest,
    repository: RepositoryDep,
) -> WorkflowRunRemediation:
    """Dismiss a workflow remediation candidate."""
    try:
        return await repository.dismiss_remediation_candidate(
            remediation_id,
            actor=request.actor,
            reason=request.reason,
        )
    except WorkflowRunRemediationNotFoundError as exc:
        raise_not_found("Workflow remediation not found", exc)
    except ValueError as exc:
        raise_conflict(str(exc), exc)


__all__ = ["router", "RemediationDismissRequest"]
