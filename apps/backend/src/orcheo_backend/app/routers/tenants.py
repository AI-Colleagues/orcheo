"""Tenant admin and member-management routes."""

from __future__ import annotations
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, status
from orcheo.tenancy import (
    Role,
    Tenant,
    TenantMembership,
    TenantMembershipError,
    TenantNotFoundError,
    TenantPermissionError,
    TenantSlugConflictError,
)
from orcheo_backend.app.schemas.tenants import (
    MembershipCreateRequest,
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MeMembershipsResponse,
    TenantCreateRequest,
    TenantListResponse,
    TenantResponse,
    TenantStatusUpdateRequest,
)
from orcheo_backend.app.tenancy import (
    TenantHTTPError,
    raise_tenant_forbidden,
    raise_tenant_not_found,
    require_role,
)
from orcheo_backend.app.tenancy.dependencies import (
    TenantContextDep,
    TenantServiceDep,
)


__all__ = ["admin_router", "router"]


admin_router = APIRouter(prefix="/admin/tenants", tags=["admin", "tenants"])
router = APIRouter(prefix="/tenants", tags=["tenants"])


def _to_tenant_response(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        quotas=tenant.quotas,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


def _to_membership_response(membership: TenantMembership) -> MembershipResponse:
    return MembershipResponse(
        id=membership.id,
        tenant_id=membership.tenant_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
    )


@admin_router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant(
    payload: TenantCreateRequest,
    service: TenantServiceDep,
) -> TenantResponse:
    """Create a tenant and assign the owner membership."""
    try:
        tenant, _ = service.create_tenant(
            slug=payload.slug,
            name=payload.name,
            owner_user_id=payload.owner_user_id,
            quotas=payload.quotas,
        )
    except TenantSlugConflictError as exc:
        raise TenantHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=f"Tenant slug already exists: {exc}",
            error_code="tenant.slug_conflict",
        ) from exc
    return _to_tenant_response(tenant)


@admin_router.get("", response_model=TenantListResponse)
def list_tenants(
    service: TenantServiceDep,
    include_inactive: bool = False,
) -> TenantListResponse:
    """List all tenants visible to operators."""
    tenants = service.list_tenants(include_inactive=include_inactive)
    return TenantListResponse(
        tenants=[_to_tenant_response(t) for t in tenants],
    )


@admin_router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: UUID,
    service: TenantServiceDep,
) -> TenantResponse:
    """Return the tenant for a given identifier."""
    try:
        tenant = service.repository.get_tenant(tenant_id)
    except TenantNotFoundError:
        raise_tenant_not_found()
    return _to_tenant_response(tenant)


@admin_router.patch("/{tenant_id}/status", response_model=TenantResponse)
def update_tenant_status(
    tenant_id: UUID,
    payload: TenantStatusUpdateRequest,
    service: TenantServiceDep,
) -> TenantResponse:
    """Change a tenant's lifecycle status."""
    try:
        tenant = service.repository.update_status(tenant_id, payload.status)
    except TenantNotFoundError:
        raise_tenant_not_found()
    return _to_tenant_response(tenant)


@admin_router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: UUID,
    service: TenantServiceDep,
) -> None:
    """Hard-delete a tenant; cascades memberships."""
    try:
        service.hard_delete_tenant(tenant_id)
    except TenantNotFoundError:
        raise_tenant_not_found()


@router.get("/me", response_model=MeMembershipsResponse)
def list_my_memberships(
    service: TenantServiceDep,
    context: TenantContextDep,
) -> MeMembershipsResponse:
    """Return the memberships for the calling principal."""
    pairs = service.memberships_for(user_id=context.user_id)
    entries = [
        {
            "tenant_id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "role": membership.role,
            "status": tenant.status,
        }
        for tenant, membership in pairs
    ]
    return MeMembershipsResponse.model_validate({"memberships": entries})


@router.post(
    "/{slug}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def add_tenant_member(
    slug: str,
    payload: MembershipCreateRequest,
    service: TenantServiceDep,
    context: TenantContextDep,
) -> MembershipResponse:
    """Add a member to a tenant; requires admin or owner role."""
    try:
        tenant = service.repository.get_tenant_by_slug(slug)
    except TenantNotFoundError:
        raise_tenant_not_found()

    if context.tenant_id != tenant.id:
        raise_tenant_forbidden(
            "Cannot manage members for a tenant you are not actively scoped to",
            error_code="tenant.scope_mismatch",
        )

    try:
        membership = service.invite_member(
            tenant_id=tenant.id,
            user_id=payload.user_id,
            role=payload.role,
            actor_role=context.role,
        )
    except TenantMembershipError as exc:
        raise TenantHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="tenant.membership_conflict",
        ) from exc
    except TenantPermissionError as exc:
        raise_tenant_forbidden(str(exc))
    return _to_membership_response(membership)


@router.patch(
    "/{slug}/members/{user_id}",
    response_model=MembershipResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def update_tenant_member_role(
    slug: str,
    user_id: str,
    payload: MembershipRoleUpdateRequest,
    service: TenantServiceDep,
    context: TenantContextDep,
) -> MembershipResponse:
    """Change a member's role inside the tenant."""
    try:
        tenant = service.repository.get_tenant_by_slug(slug)
    except TenantNotFoundError:
        raise_tenant_not_found()
    if context.tenant_id != tenant.id:
        raise_tenant_forbidden(
            "Cannot manage members for a tenant you are not actively scoped to",
            error_code="tenant.scope_mismatch",
        )
    try:
        membership = service.update_member_role(
            tenant_id=tenant.id,
            user_id=user_id,
            role=payload.role,
            actor_role=context.role,
        )
    except TenantMembershipError:
        raise_tenant_not_found("Membership not found")
    except TenantPermissionError as exc:
        raise_tenant_forbidden(str(exc))
    return _to_membership_response(membership)


@router.delete(
    "/{slug}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def remove_tenant_member(
    slug: str,
    user_id: str,
    service: TenantServiceDep,
    context: TenantContextDep,
) -> None:
    """Remove a member from the tenant."""
    try:
        tenant = service.repository.get_tenant_by_slug(slug)
    except TenantNotFoundError:
        raise_tenant_not_found()
    if context.tenant_id != tenant.id:
        raise_tenant_forbidden(
            "Cannot manage members for a tenant you are not actively scoped to",
            error_code="tenant.scope_mismatch",
        )
    try:
        service.remove_member(
            tenant_id=tenant.id, user_id=user_id, actor_role=context.role
        )
    except TenantMembershipError:
        raise_tenant_not_found("Membership not found")
    except TenantPermissionError as exc:
        raise_tenant_forbidden(str(exc))


_RoleParam = Annotated[Role, Depends(require_role(Role.ADMIN))]
