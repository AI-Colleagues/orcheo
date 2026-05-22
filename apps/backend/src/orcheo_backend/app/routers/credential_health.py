"""Credential health routes."""

from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, HTTPException, status
from orcheo.models import CredentialHealthStatus
from orcheo_backend.app.credential_readiness import (
    collect_workflow_credential_placeholders,
)
from orcheo_backend.app.dependencies import (
    CredentialServiceDep,
    RepositoryDep,
    VaultDep,
    WorkspaceContextDep,
    credential_context_from_workflow,
    resolve_workflow_ref_id,
    resolve_workflow_workspace_id,
)
from orcheo_backend.app.errors import raise_not_found
from orcheo_backend.app.history_utils import health_report_to_response
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.credentials import (
    CredentialHealthResponse,
    CredentialReadinessItem,
    CredentialReadinessResponse,
    CredentialValidationRequest,
)


router = APIRouter()


@router.get(
    "/workflows/{workflow_ref}/credentials/readiness",
    response_model=CredentialReadinessResponse,
)
async def get_workflow_credential_readiness(
    workflow_ref: str,
    repository: RepositoryDep,
    vault: VaultDep,
    workspace: WorkspaceContextDep,
) -> CredentialReadinessResponse:
    """Return whether referenced workflow credentials are available in the vault."""
    tid = str(workspace.workspace_id)
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=tid
    )
    try:
        await repository.get_workflow(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)

    try:
        version = await repository.get_latest_version(workflow_uuid)
    except WorkflowVersionNotFoundError:
        return CredentialReadinessResponse(
            workflow_id=str(workflow_uuid),
            status="not_required",
            referenced_credentials=[],
            available_credentials=[],
            missing_credentials=[],
        )

    graph_payload = version.graph if isinstance(version.graph, dict) else {}
    runnable_config = (
        version.runnable_config if isinstance(version.runnable_config, dict) else None
    )
    placeholders = collect_workflow_credential_placeholders(
        graph_payload,
        runnable_config,
    )

    workspace_id = await resolve_workflow_workspace_id(
        repository, workflow_uuid, workspace_id=tid
    )
    context = credential_context_from_workflow(workflow_uuid, workspace_id=workspace_id)
    credentials = vault.list_credentials(context=context, workspace_id=tid)
    credentials_by_name = {
        metadata.name.strip().lower(): metadata for metadata in credentials
    }

    referenced_credentials: list[CredentialReadinessItem] = []
    available_credentials: list[str] = []
    missing_credentials: list[str] = []

    for name in sorted(placeholders, key=str.lower):
        metadata = credentials_by_name.get(name.strip().lower())
        if metadata is None:
            missing_credentials.append(name)
        else:
            available_credentials.append(name)
        referenced_credentials.append(
            CredentialReadinessItem(
                name=name,
                placeholders=sorted(placeholders[name]),
                available=metadata is not None,
                credential_id=str(metadata.id) if metadata is not None else None,
                provider=metadata.provider if metadata is not None else None,
            )
        )

    status_value: Literal["ready", "missing", "not_required"]
    if not referenced_credentials:
        status_value = "not_required"
    elif missing_credentials:
        status_value = "missing"
    else:
        status_value = "ready"

    return CredentialReadinessResponse(
        workflow_id=str(workflow_uuid),
        status=status_value,
        referenced_credentials=referenced_credentials,
        available_credentials=available_credentials,
        missing_credentials=missing_credentials,
    )


@router.get(
    "/workflows/{workflow_ref}/credentials/health",
    response_model=CredentialHealthResponse,
)
async def get_workflow_credential_health(
    workflow_ref: str,
    repository: RepositoryDep,
    service: CredentialServiceDep,
    workspace: WorkspaceContextDep,
) -> CredentialHealthResponse:
    """Return cached credential health information for a workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=str(workspace.workspace_id)
    )
    try:
        await repository.get_workflow(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential health service is not configured.",
        )

    report = service.get_report(workflow_uuid)
    if report is None:
        return CredentialHealthResponse(
            workflow_id=str(workflow_uuid),
            status=CredentialHealthStatus.UNKNOWN,
            checked_at=None,
            credentials=[],
        )
    return health_report_to_response(report)


@router.post(
    "/workflows/{workflow_ref}/credentials/validate",
    response_model=CredentialHealthResponse,
)
async def validate_workflow_credentials(
    workflow_ref: str,
    request: CredentialValidationRequest,
    repository: RepositoryDep,
    service: CredentialServiceDep,
    workspace: WorkspaceContextDep,
) -> CredentialHealthResponse:
    """Trigger validation of workflow credentials and return the updated report."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, workspace_id=str(workspace.workspace_id)
    )
    try:
        await repository.get_workflow(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential health service is not configured.",
        )

    report = await service.ensure_workflow_health(workflow_uuid, actor=request.actor)
    if not report.is_healthy:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Credentials failed validation.",
                "failures": report.failures,
            },
        )
    return health_report_to_response(report)


__all__ = [
    "get_workflow_credential_readiness",
    "get_workflow_credential_health",
    "router",
    "validate_workflow_credentials",
]
