"""Credential governance alert routes."""

from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter
from orcheo.vault import GovernanceAlertNotFoundError, WorkflowScopeError
from orcheo_backend.app.credential_utils import alert_to_response
from orcheo_backend.app.dependencies import (
    IncludeAcknowledgedQuery,
    RepositoryDep,
    VaultDep,
    WorkflowRefQuery,
    WorkspaceContextDep,
    credential_context_from_workflow,
    resolve_optional_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_not_found, raise_scope_error
from orcheo_backend.app.schemas.governance import (
    AlertAcknowledgeRequest,
    GovernanceAlertResponse,
)


router = APIRouter()


@router.get(
    "/credentials/governance-alerts",
    response_model=list[GovernanceAlertResponse],
)
async def list_governance_alerts(
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
    include_acknowledged: IncludeAcknowledgedQuery = False,
) -> list[GovernanceAlertResponse]:
    """List governance alerts for the caller."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    alerts = vault.list_alerts(
        context=context,
        include_acknowledged=include_acknowledged,
    )
    return [alert_to_response(alert) for alert in alerts]


@router.post(
    "/credentials/governance-alerts/{alert_id}/acknowledge",
    response_model=GovernanceAlertResponse,
)
async def acknowledge_governance_alert(
    alert_id: UUID,
    request: AlertAcknowledgeRequest,
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> GovernanceAlertResponse:
    """Acknowledge an outstanding governance alert."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    try:
        alert = vault.acknowledge_alert(alert_id, actor=request.actor, context=context)
        return alert_to_response(alert)
    except GovernanceAlertNotFoundError as exc:
        raise_not_found("Governance alert not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)


__all__ = ["router"]
