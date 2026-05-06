"""Workspace admin and member-management routes."""

from __future__ import annotations
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from orcheo.workspace import (
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
    WorkspaceStatus,
)
from orcheo_backend.app.schemas.workspaces import (
    ActiveWorkspaceResponse,
    MembershipCreateRequest,
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MeMembershipsResponse,
    WorkspaceAuditEventListResponse,
    WorkspaceAuditEventResponse,
    WorkspaceCreateRequest,
    WorkspaceListResponse,
    WorkspaceResponse,
    WorkspaceStatusUpdateRequest,
)
from orcheo_backend.app.workspace import (
    WorkspaceHTTPError,
    raise_workspace_forbidden,
    raise_workspace_not_found,
    require_role,
)
from orcheo_backend.app.workspace.dependencies import (
    WorkspaceContextDep,
    WorkspaceServiceDep,
)


__all__ = [
    "admin_router",
    "router",
]


admin_router = APIRouter(
    prefix="/admin/workspaces",
    tags=["admin", "workspaces"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)
router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _to_workspace_response(workspace: Workspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        status=workspace.status,
        quotas=workspace.quotas,
        deleted_at=workspace.deleted_at,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _to_membership_response(membership: WorkspaceMembership) -> MembershipResponse:
    return MembershipResponse(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
    )


def _to_audit_event_response(event: WorkspaceAuditEvent) -> WorkspaceAuditEventResponse:
    """Serialize a workspace audit event for API responses."""
    return WorkspaceAuditEventResponse(
        id=event.id,
        workspace_id=event.workspace_id,
        action=event.action,
        actor=event.actor,
        subject=event.subject,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        details=event.details,
        created_at=event.created_at,
    )


@admin_router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    payload: WorkspaceCreateRequest,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    """Create a workspace and assign the owner membership."""
    try:
        workspace, _ = service.create_workspace(
            slug=payload.slug,
            name=payload.name,
            owner_user_id=payload.owner_user_id,
            quotas=payload.quotas,
        )
    except WorkspaceSlugConflictError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=f"Workspace slug already exists: {exc}",
            error_code="workspace.slug_conflict",
        ) from exc
    return _to_workspace_response(workspace)


@admin_router.get("", response_model=WorkspaceListResponse)
def list_workspaces(
    service: WorkspaceServiceDep,
    include_inactive: bool = False,
) -> WorkspaceListResponse:
    """List all workspaces visible to operators."""
    workspaces = service.list_workspaces(include_inactive=include_inactive)
    return WorkspaceListResponse(
        workspaces=[_to_workspace_response(workspace) for workspace in workspaces],
    )


@admin_router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    """Return the workspace for a given identifier."""
    try:
        workspace = service.repository.get_workspace(workspace_id)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    return _to_workspace_response(workspace)


@admin_router.patch("/{workspace_id}/status", response_model=WorkspaceResponse)
def update_workspace_status(
    workspace_id: UUID,
    payload: WorkspaceStatusUpdateRequest,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    """Change a workspace's lifecycle status."""
    try:
        if payload.status is WorkspaceStatus.SUSPENDED:
            workspace = service.deactivate_workspace(workspace_id)
        elif payload.status is WorkspaceStatus.ACTIVE:
            workspace = service.reactivate_workspace(workspace_id)
        elif payload.status is WorkspaceStatus.DELETED:
            workspace = service.soft_delete_workspace(workspace_id)
        else:
            workspace = service.repository.update_status(workspace_id, payload.status)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    return _to_workspace_response(workspace)


@admin_router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
) -> None:
    """Hard-delete a workspace; cascades memberships."""
    try:
        service.hard_delete_workspace(workspace_id)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()


@admin_router.get(
    "/{workspace_id}/audit-events",
    response_model=WorkspaceAuditEventListResponse,
)
def list_workspace_audit_events(
    workspace_id: UUID,
    service: WorkspaceServiceDep,
    limit: int = Query(100, ge=1, le=500),
) -> WorkspaceAuditEventListResponse:
    """Return the audit events recorded for a workspace."""
    try:
        service.repository.get_workspace(workspace_id)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()

    events = service.repository.list_audit_events(workspace_id, limit=limit)
    return WorkspaceAuditEventListResponse(
        audit_events=[_to_audit_event_response(event) for event in events]
    )


@admin_router.post(
    "/purge-deleted",
    status_code=status.HTTP_204_NO_CONTENT,
)
def purge_deleted_workspaces(
    service: WorkspaceServiceDep,
    retention_days: int = 30,
) -> None:
    """Hard-delete workspaces whose soft-delete retention window has expired."""
    service.purge_deleted_workspaces(retention_days=retention_days)


@router.get("/me", response_model=MeMembershipsResponse)
def list_my_memberships(
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> MeMembershipsResponse:
    """Return the memberships for the calling principal."""
    pairs = service.memberships_for(user_id=context.user_id)
    entries = [
        {
            "workspace_id": workspace.id,
            "slug": workspace.slug,
            "name": workspace.name,
            "role": membership.role,
            "status": workspace.status,
        }
        for workspace, membership in pairs
    ]
    return MeMembershipsResponse.model_validate({"memberships": entries})


@router.get("/active", response_model=ActiveWorkspaceResponse)
def get_active_workspace(
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> ActiveWorkspaceResponse:
    """Return the active workspace currently resolved for the request."""
    workspace = service.repository.get_workspace(context.workspace_id)
    return ActiveWorkspaceResponse(
        workspace_id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        role=context.role,
    )


@router.post(
    "/{slug}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def add_workspace_member(
    slug: str,
    payload: MembershipCreateRequest,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> MembershipResponse:
    """Add a member to a workspace; requires admin or owner role."""
    try:
        workspace = service.repository.get_workspace_by_slug(slug)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()

    if context.workspace_id != workspace.id:
        raise_workspace_forbidden(
            "Cannot manage members for a workspace you are not actively scoped to",
            error_code="workspace.scope_mismatch",
        )

    try:
        membership = service.invite_member(
            workspace_id=workspace.id,
            user_id=payload.user_id,
            role=payload.role,
            actor_role=context.role,
        )
    except WorkspaceMembershipError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_conflict",
        ) from exc
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))
    return _to_membership_response(membership)


@router.patch(
    "/{slug}/members/{user_id}",
    response_model=MembershipResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def update_workspace_member_role(
    slug: str,
    user_id: str,
    payload: MembershipRoleUpdateRequest,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> MembershipResponse:
    """Change a member's role inside the workspace."""
    try:
        workspace = service.repository.get_workspace_by_slug(slug)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    if context.workspace_id != workspace.id:
        raise_workspace_forbidden(
            "Cannot manage members for a workspace you are not actively scoped to",
            error_code="workspace.scope_mismatch",
        )
    try:
        membership = service.update_member_role(
            workspace_id=workspace.id,
            user_id=user_id,
            role=payload.role,
            actor_role=context.role,
        )
    except WorkspaceMembershipError:
        raise_workspace_not_found("Membership not found")
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))
    return _to_membership_response(membership)


@router.delete(
    "/{slug}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def remove_workspace_member(
    slug: str,
    user_id: str,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> None:
    """Remove a member from the workspace."""
    try:
        workspace = service.repository.get_workspace_by_slug(slug)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    if context.workspace_id != workspace.id:
        raise_workspace_forbidden(
            "Cannot manage members for a workspace you are not actively scoped to",
            error_code="workspace.scope_mismatch",
        )
    try:
        service.remove_member(
            workspace_id=workspace.id, user_id=user_id, actor_role=context.role
        )
    except WorkspaceMembershipError:
        raise_workspace_not_found("Membership not found")
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))


_RoleParam = Annotated[Role, Depends(require_role(Role.ADMIN))]
