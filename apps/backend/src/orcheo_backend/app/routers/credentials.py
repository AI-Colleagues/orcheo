"""Credential metadata routes."""

from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, HTTPException, Response, status
from orcheo.vault import (
    CredentialNotFoundError,
    DuplicateCredentialNameError,
    WorkflowScopeError,
)
from orcheo_backend.app.credential_utils import (
    credential_to_response,
    scope_from_access,
)
from orcheo_backend.app.dependencies import (
    RepositoryDep,
    VaultDep,
    WorkflowRefQuery,
    credential_context_from_workflow,
    resolve_optional_workflow_ref_id,
)
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    raise_not_found,
    raise_scope_error,
)
from orcheo_backend.app.schemas.credentials import (
    CredentialCreateRequest,
    CredentialSecretResponse,
    CredentialUpdateRequest,
    CredentialVaultEntryResponse,
)
from orcheo_backend.app.workspace import WorkspaceContextDep
from orcheo_backend.app.workspace_governance import ensure_workspace_credential_quota


router = APIRouter()


@router.get(
    "/credentials",
    response_model=list[CredentialVaultEntryResponse],
)
async def list_credentials(
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> list[CredentialVaultEntryResponse]:
    """Return credential metadata visible to the caller."""
    tid = str(workspace.workspace_id)
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    if resolved_workflow_id is None:
        credentials = vault.list_all_credentials(workspace_id=tid)
    else:
        context = credential_context_from_workflow(resolved_workflow_id)
        credentials = vault.list_credentials(context=context, workspace_id=tid)
    return [credential_to_response(metadata) for metadata in credentials]


@router.post(
    "/credentials",
    response_model=CredentialVaultEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    request: CredentialCreateRequest,
    repository: RepositoryDep,
    vault: VaultDep,
    workspace: WorkspaceContextDep,
) -> CredentialVaultEntryResponse:
    """Persist a new credential in the vault."""
    workflow_id = await resolve_optional_workflow_ref_id(
        repository, request.workflow_id
    )
    if request.access == "scoped" and workflow_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="workflow_id is required when access is set to scoped",
        )
    scope = scope_from_access(request.access, workflow_id)
    try:
        await ensure_workspace_credential_quota(vault, workspace)
        metadata = vault.create_credential(
            name=request.name,
            provider=request.provider,
            scopes=request.scopes,
            secret=request.secret,
            actor=request.actor,
            scope=scope,
            kind=request.kind,
            workspace_id=str(workspace.workspace_id),
        )
    except DuplicateCredentialNameError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except WorkspaceQuotaExceededError as exc:
        raise exc.as_http_exception() from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return credential_to_response(metadata)


@router.get(
    "/credentials/{credential_id}/secret",
    response_model=CredentialSecretResponse,
)
async def reveal_credential_secret(
    credential_id: UUID,
    vault: VaultDep,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    workflow_id: WorkflowRefQuery = None,
) -> CredentialSecretResponse:
    """Reveal and return the decrypted credential secret."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(resolved_workflow_id)
    try:
        secret = vault.reveal_secret(credential_id=credential_id, context=context)
    except CredentialNotFoundError as exc:
        raise_not_found("Credential not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)
    from orcheo.workspace import WorkspaceAuditEvent
    from orcheo_backend.app.workspace import get_workspace_repository

    try:
        get_workspace_repository().record_audit_event(
            WorkspaceAuditEvent(
                workspace_id=workspace.workspace_id,
                action="vault.read",
                actor=workspace.user_id,
                subject=str(credential_id),
                resource_type="credential",
                resource_id=str(credential_id),
            )
        )
    except Exception:  # pragma: no cover - audit is best effort
        pass
    return CredentialSecretResponse(id=str(credential_id), secret=secret)


@router.patch(
    "/credentials/{credential_id}",
    response_model=CredentialVaultEntryResponse,
)
async def update_credential(
    credential_id: UUID,
    request: CredentialUpdateRequest,
    repository: RepositoryDep,
    vault: VaultDep,
    workflow_id: WorkflowRefQuery = None,
) -> CredentialVaultEntryResponse:
    """Update credential metadata and optionally rotate the secret."""
    query_workflow_id = await resolve_optional_workflow_ref_id(repository, workflow_id)
    body_workflow_id = await resolve_optional_workflow_ref_id(
        repository, request.workflow_id
    )
    effective_workflow_id = query_workflow_id or body_workflow_id
    if request.access == "scoped" and effective_workflow_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="workflow_id is required when access is set to scoped",
        )
    context = credential_context_from_workflow(effective_workflow_id)
    scope = (
        scope_from_access(request.access, effective_workflow_id)
        if request.access is not None
        else None
    )
    try:
        metadata = vault.update_credential(
            credential_id=credential_id,
            actor=request.actor,
            name=request.name,
            provider=request.provider,
            secret=request.secret,
            scope=scope,
            context=context,
        )
    except CredentialNotFoundError as exc:
        raise_not_found("Credential not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)
    except DuplicateCredentialNameError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return credential_to_response(metadata)


@router.delete(
    "/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_credential(
    credential_id: UUID,
    vault: VaultDep,
    repository: RepositoryDep,
    workflow_id: WorkflowRefQuery = None,
) -> Response:
    """Delete a credential."""
    resolved_workflow_id = await resolve_optional_workflow_ref_id(
        repository, workflow_id
    )
    context = credential_context_from_workflow(resolved_workflow_id)
    try:
        vault.delete_credential(credential_id, context=context)
    except CredentialNotFoundError as exc:
        raise_not_found("Credential not found", exc)
    except WorkflowScopeError as exc:
        raise_scope_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
