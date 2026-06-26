"""FastAPI endpoints for service token management.

Service tokens are scoped to the workspace that mints them: the minting
workspace is the only workspace the token may ever resolve to, and tokens
belonging to other workspaces are never listed or addressable here.
"""

from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from orcheo.workspace import WorkspaceAuditEvent, WorkspaceContext
from orcheo_backend.app.authentication import (
    AuthorizationPolicy,
    ServiceTokenManager,
    ServiceTokenRecord,
    get_authorization_policy,
    get_service_token_manager,
)
from orcheo_backend.app.schemas.service_tokens import (
    CreateServiceTokenRequest,
    RevokeServiceTokenRequest,
    RotateServiceTokenRequest,
    ServiceTokenListResponse,
    ServiceTokenResponse,
)
from orcheo_backend.app.workspace import WorkspaceContextDep, get_workspace_repository


router = APIRouter(prefix="/admin/service-tokens", tags=["admin", "tokens"])


def _record_to_response(
    record: ServiceTokenRecord,
    *,
    secret: str | None = None,
    message: str | None = None,
) -> ServiceTokenResponse:
    """Convert ServiceTokenRecord to API response."""
    return ServiceTokenResponse(
        identifier=record.identifier,
        name=record.name,
        secret=secret,
        secret_preview=record.secret_preview,
        scopes=sorted(record.scopes),
        workspace_ids=sorted(record.workspace_ids),
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        last_used_at=record.last_used_at,
        use_count=record.use_count,
        revoked_at=record.revoked_at,
        revocation_reason=record.revocation_reason,
        rotated_to=record.rotated_to,
        message=message,
    )


async def _require_workspace_token(
    token_id: str,
    token_manager: ServiceTokenManager,
    workspace: WorkspaceContext,
) -> ServiceTokenRecord:
    """Return ``token_id`` only when it belongs to the active workspace.

    Tokens owned by other workspaces are reported as missing so callers cannot
    probe for or act on tokens outside their own workspace.
    """
    record = await token_manager._repository.find_by_id(token_id)
    if record is None or record.workspace_id != str(workspace.workspace_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Service token '{token_id}' not found",
                "code": "token.not_found",
            },
        )
    return record


@router.post(
    "", response_model=ServiceTokenResponse, status_code=status.HTTP_201_CREATED
)
async def create_service_token(
    request: CreateServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    workspace: WorkspaceContextDep,
) -> ServiceTokenResponse:
    """Mint a new service token scoped to the active workspace.

    Any authenticated member of the workspace may mint a token. The token can
    only ever access the workspace that minted it; the secret is shown once in
    the response and cannot be retrieved later.
    """
    policy.require_authenticated()
    policy.require_scopes(*request.scopes)

    token_manager = get_service_token_manager()
    workspace_id = str(workspace.workspace_id)

    secret, record = await token_manager.mint(
        name=request.name,
        scopes=request.scopes,
        workspace_ids=[workspace_id],
        expires_in=request.expires_in_seconds,
        workspace_id=workspace_id,
    )
    try:
        get_workspace_repository().record_audit_event(
            WorkspaceAuditEvent(
                workspace_id=workspace.workspace_id,
                action="service_token.created",
                actor=policy.context.subject,
                subject=record.identifier,
                resource_type="service_token",
                resource_id=record.identifier,
                details={"scopes": sorted(record.scopes)},
            )
        )
    except Exception:  # pragma: no cover - audit is best effort
        pass

    return _record_to_response(
        record,
        secret=secret,
        message="Store this token securely. It will not be shown again.",
    )


@router.get("", response_model=ServiceTokenListResponse)
async def list_service_tokens(
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    workspace: WorkspaceContextDep,
) -> ServiceTokenListResponse:
    """List service tokens owned by the active workspace.

    Tokens belonging to other workspaces are never returned. Secrets are never
    included in the list.
    """
    policy.require_authenticated()

    token_manager = get_service_token_manager()
    records = await token_manager._repository.list_for_workspace(
        str(workspace.workspace_id)
    )

    tokens = [_record_to_response(record) for record in records]
    return ServiceTokenListResponse(tokens=tokens, total=len(tokens))


@router.get("/{token_id}", response_model=ServiceTokenResponse)
async def get_service_token(
    token_id: str,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    workspace: WorkspaceContextDep,
) -> ServiceTokenResponse:
    """Get details for a service token owned by the active workspace."""
    policy.require_authenticated()

    token_manager = get_service_token_manager()
    record = await _require_workspace_token(token_id, token_manager, workspace)

    return _record_to_response(record)


@router.post("/{token_id}/rotate", response_model=ServiceTokenResponse)
async def rotate_service_token(
    token_id: str,
    request: RotateServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    workspace: WorkspaceContextDep,
) -> ServiceTokenResponse:
    """Rotate a workspace service token, generating a new secret.

    The old token remains valid during the overlap period. The replacement
    token stays scoped to the same workspace. The new secret is shown once.
    """
    policy.require_authenticated()

    token_manager = get_service_token_manager()
    await _require_workspace_token(token_id, token_manager, workspace)

    secret, new_record = await token_manager.rotate(
        token_id,
        overlap_seconds=request.overlap_seconds,
        expires_in=request.expires_in_seconds,
    )

    message = (
        f"New token created. Old token '{token_id}' "
        f"valid for {request.overlap_seconds}s."
    )
    try:
        get_workspace_repository().record_audit_event(
            WorkspaceAuditEvent(
                workspace_id=workspace.workspace_id,
                action="service_token.rotated",
                actor=policy.context.subject,
                subject=token_id,
                resource_type="service_token",
                resource_id=token_id,
                details={"replacement": new_record.identifier},
            )
        )
    except Exception:  # pragma: no cover - audit is best effort
        pass
    return _record_to_response(new_record, secret=secret, message=message)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_service_token(
    token_id: str,
    request: RevokeServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    workspace: WorkspaceContextDep,
) -> None:
    """Revoke a workspace service token immediately.

    The token will no longer be usable for authentication.
    """
    policy.require_authenticated()

    token_manager = get_service_token_manager()
    await _require_workspace_token(token_id, token_manager, workspace)

    await token_manager.revoke(token_id, reason=request.reason)
    try:
        get_workspace_repository().record_audit_event(
            WorkspaceAuditEvent(
                workspace_id=workspace.workspace_id,
                action="service_token.revoked",
                actor=policy.context.subject,
                subject=token_id,
                resource_type="service_token",
                resource_id=token_id,
                details={"reason": request.reason},
            )
        )
    except Exception:  # pragma: no cover - audit is best effort
        pass


__all__ = ["router"]
