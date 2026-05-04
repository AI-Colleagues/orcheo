"""FastAPI endpoints for service token management."""

from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from orcheo_backend.app.authentication import (
    AuthorizationPolicy,
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
from orcheo_backend.app.tenancy import TenantContextDep


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
        secret=secret,
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


@router.post(
    "", response_model=ServiceTokenResponse, status_code=status.HTTP_201_CREATED
)
async def create_service_token(
    request: CreateServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    tenant: TenantContextDep,
) -> ServiceTokenResponse:
    """Create a new service token.

    Requires 'admin:tokens:write' scope.
    The secret is only shown once in the response and cannot be retrieved later.
    """
    policy.require_authenticated()
    policy.require_scopes("admin:tokens:write")

    token_manager = get_service_token_manager()

    secret, record = await token_manager.mint(
        identifier=request.identifier,
        scopes=request.scopes,
        workspace_ids=request.workspace_ids,
        expires_in=request.expires_in_seconds,
        tenant_id=str(tenant.tenant_id),
    )

    return _record_to_response(
        record,
        secret=secret,
        message="Store this token securely. It will not be shown again.",
    )


@router.get("", response_model=ServiceTokenListResponse)
async def list_service_tokens(
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> ServiceTokenListResponse:
    """List all service tokens.

    Requires 'admin:tokens:read' scope.
    Secrets are never returned in the list.
    """
    policy.require_authenticated()
    policy.require_scopes("admin:tokens:read")

    token_manager = get_service_token_manager()
    records = await token_manager.all()

    tokens = [_record_to_response(record) for record in records]
    return ServiceTokenListResponse(tokens=tokens, total=len(tokens))


@router.get("/{token_id}", response_model=ServiceTokenResponse)
async def get_service_token(
    token_id: str,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> ServiceTokenResponse:
    """Get details for a specific service token.

    Requires 'admin:tokens:read' scope.
    """
    policy.require_authenticated()
    policy.require_scopes("admin:tokens:read")

    token_manager = get_service_token_manager()
    record = await token_manager._repository.find_by_id(token_id)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Service token '{token_id}' not found",
                "code": "token.not_found",
            },
        )

    return _record_to_response(record)


@router.post("/{token_id}/rotate", response_model=ServiceTokenResponse)
async def rotate_service_token(
    token_id: str,
    request: RotateServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> ServiceTokenResponse:
    """Rotate a service token, generating a new secret.

    Requires 'admin:tokens:write' scope.
    The old token remains valid during the overlap period.
    The new secret is only shown once.
    """
    policy.require_authenticated()
    policy.require_scopes("admin:tokens:write")

    token_manager = get_service_token_manager()

    try:
        secret, new_record = await token_manager.rotate(
            token_id,
            overlap_seconds=request.overlap_seconds,
            expires_in=request.expires_in_seconds,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Service token '{token_id}' not found",
                "code": "token.not_found",
            },
        ) from None

    message = (
        f"New token created. Old token '{token_id}' "
        f"valid for {request.overlap_seconds}s."
    )
    return _record_to_response(new_record, secret=secret, message=message)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_service_token(
    token_id: str,
    request: RevokeServiceTokenRequest,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
) -> None:
    """Revoke a service token immediately.

    Requires 'admin:tokens:write' scope.
    The token will no longer be usable for authentication.
    """
    policy.require_authenticated()
    policy.require_scopes("admin:tokens:write")

    token_manager = get_service_token_manager()

    try:
        await token_manager.revoke(token_id, reason=request.reason)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Service token '{token_id}' not found",
                "code": "token.not_found",
            },
        ) from None


__all__ = ["router"]
