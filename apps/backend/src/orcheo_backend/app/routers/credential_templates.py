"""Credential template routes."""

from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, HTTPException, Response, status
from orcheo.vault import (
    CredentialTemplateNotFoundError,
    WorkflowScopeError,
)
from orcheo_backend.app.credential_utils import (
    build_oauth_tokens,
    build_policy,
    build_scope,
    template_to_response,
)
from orcheo_backend.app.dependencies import (
    CredentialServiceDep,
    RepositoryDep,
    VaultDep,
    WorkflowRefQuery,
    WorkspaceContextDep,
    credential_context_from_workflow,
    resolve_optional_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_not_found, raise_scope_error
from orcheo_backend.app.schemas.credentials import (
    CredentialIssuanceRequest,
    CredentialIssuanceResponse,
    CredentialTemplateCreateRequest,
    CredentialTemplateResponse,
    CredentialTemplateUpdateRequest,
)


router = APIRouter()


@router.get(
    "/credentials/templates",
    response_model=list[CredentialTemplateResponse],
)
async def list_credential_templates(
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> list[CredentialTemplateResponse]:
    """List credential templates visible to the caller."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    templates = vault.list_templates(
        context=context, workspace_id=str(workspace.workspace_id)
    )
    return [template_to_response(template) for template in templates]


@router.post(
    "/credentials/templates",
    response_model=CredentialTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_credential_template(
    request: CredentialTemplateCreateRequest,
    vault: VaultDep,
    workspace: WorkspaceContextDep,
) -> CredentialTemplateResponse:
    """Create a new credential template."""
    scope = build_scope(request.scope)
    policy = build_policy(request.issuance_policy)
    template = vault.create_template(
        name=request.name,
        provider=request.provider,
        scopes=request.scopes,
        actor=request.actor,
        description=request.description,
        scope=scope,
        kind=request.kind,
        issuance_policy=policy,
        workspace_id=str(workspace.workspace_id),
    )
    return template_to_response(template)


@router.get(
    "/credentials/templates/{template_id}",
    response_model=CredentialTemplateResponse,
)
async def get_credential_template(
    template_id: UUID,
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> CredentialTemplateResponse:
    """Return a single credential template."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    try:
        template = vault.get_template(template_id=template_id, context=context)
        return template_to_response(template)
    except CredentialTemplateNotFoundError as exc:
        raise_not_found("Credential template not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)


@router.patch(
    "/credentials/templates/{template_id}",
    response_model=CredentialTemplateResponse,
)
async def update_credential_template(
    template_id: UUID,
    request: CredentialTemplateUpdateRequest,
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> CredentialTemplateResponse:
    """Update credential template metadata."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    scope = build_scope(request.scope)
    policy = build_policy(request.issuance_policy)

    try:
        template = vault.update_template(
            template_id=template_id,
            actor=request.actor,
            name=request.name,
            description=request.description,
            scopes=request.scopes,
            scope=scope,
            kind=request.kind,
            issuance_policy=policy,
            context=context,
        )
        return template_to_response(template)
    except CredentialTemplateNotFoundError as exc:
        raise_not_found("Credential template not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)


@router.delete(
    "/credentials/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_credential_template(
    template_id: UUID,
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> Response:
    """Delete a credential template."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(
        resolved_workflow_id, workspace_id=str(workspace.workspace_id)
    )
    try:
        vault.delete_template(template_id, context=context)
    except CredentialTemplateNotFoundError as exc:
        raise_not_found("Credential template not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/credentials/templates/{template_id}/issue",
    response_model=CredentialIssuanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_credential_from_template(
    template_id: UUID,
    request: CredentialIssuanceRequest,
    repository: RepositoryDep,
    service: CredentialServiceDep,
    workspace: WorkspaceContextDep,
) -> CredentialIssuanceResponse:
    """Issue a credential based on a stored template."""
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential service is not configured.",
        )

    workflow_id = await resolve_optional_workflow_ref_id(
        repository, request.workflow_id
    )
    context = credential_context_from_workflow(
        workflow_id, workspace_id=str(workspace.workspace_id)
    )
    tokens = build_oauth_tokens(request.oauth_tokens)
    try:
        metadata = service.issue_from_template(
            template_id=template_id,
            secret=request.secret,
            actor=request.actor,
            name=request.name,
            scopes=request.scopes,
            context=context,
            oauth_tokens=tokens,
        )
    except CredentialTemplateNotFoundError as exc:
        raise_not_found("Credential template not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return CredentialIssuanceResponse(
        credential_id=str(metadata.id),
        name=metadata.name,
        provider=metadata.provider,
        kind=metadata.kind,
        template_id=str(metadata.template_id) if metadata.template_id else None,
        created_at=metadata.created_at,
        updated_at=metadata.updated_at,
    )


__all__ = ["router"]
