"""Workspace admin and member-management routes."""

from __future__ import annotations
import logging
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from orcheo.workspace import (
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceInvitation,
    WorkspaceInvitationEmailMismatchError,
    WorkspaceInvitationError,
    WorkspaceInvitationExpiredError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
    WorkspaceStatus,
)
from orcheo_backend.app.authentication import (
    RequestContext,
    authenticate_request,
    extract_email_verified,
    extract_identity,
)
from orcheo_backend.app.schemas.workspaces import (
    ActiveWorkspaceResponse,
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationCreateRequest,
    InvitationListResponse,
    InvitationResponse,
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
    "self_service_router",
]


admin_router = APIRouter(
    prefix="/admin/workspaces",
    tags=["admin", "workspaces"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)
self_service_router = APIRouter(prefix="/workspaces", tags=["workspaces"])
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
        email=membership.email,
        user_name=membership.user_name,
        role=membership.role,
        created_at=membership.created_at,
    )


def _to_invitation_response(invitation: WorkspaceInvitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        invited_by=invitation.invited_by,
        accepted_by=invitation.accepted_by,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
    )


logger = logging.getLogger(__name__)


def _verified_email(auth: RequestContext) -> tuple[str | None, bool]:
    """Return ``(email, email_verified)`` for the authenticated principal.

    First-party tokens carry ``email`` and ``email_verified`` directly (the
    latter set true only by a completed email challenge), so the invitation
    accept flow reads them straight from the token claims. The developer-login
    session is honoured as a local convenience.
    """
    email, _ = extract_identity(auth.claims)
    verified = extract_email_verified(auth.claims)
    if email is not None and verified:
        return email, True
    if auth.identity_type == "developer" and "@" in auth.subject:
        return auth.subject, True
    return email, verified


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
    context: WorkspaceContextDep,
) -> WorkspaceResponse:
    """Create a workspace and assign the owner membership."""
    try:
        workspace, _ = service.create_workspace(
            slug=payload.slug,
            name=payload.name,
            owner_user_id=payload.owner_user_id or context.user_id,
            quotas=payload.quotas,
        )
    except WorkspaceSlugConflictError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=f"Workspace slug already exists: {exc}",
            error_code="workspace.slug_conflict",
        ) from exc
    except WorkspaceMembershipLimitError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_limit_reached",
        ) from exc
    except WorkspaceMembershipError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_conflict",
        ) from exc
    return _to_workspace_response(workspace)


@self_service_router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_own_workspace(
    payload: WorkspaceCreateRequest,
    service: WorkspaceServiceDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> WorkspaceResponse:
    """Create a workspace for the authenticated principal.

    When backend authentication is disabled, ``authenticate_request`` returns an
    anonymous context. Reaching this handler means the request is allowed (the
    dependency would otherwise raise 401), so anonymous principals are treated
    as the implicit owner identity.
    """
    if payload.owner_user_id is not None and payload.owner_user_id != auth.subject:
        raise_workspace_forbidden(
            "Cannot create a workspace for another user",
            error_code="workspace.owner_mismatch",
        )

    owner_email, owner_name = extract_identity(auth.claims)
    try:
        workspace, _ = service.create_workspace(
            slug=payload.slug,
            name=payload.name,
            owner_user_id=auth.subject,
            quotas=payload.quotas,
            owner_email=owner_email,
            owner_name=owner_name,
        )
    except WorkspaceSlugConflictError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=f"Workspace slug already exists: {exc}",
            error_code="workspace.slug_conflict",
        ) from exc
    except WorkspaceMembershipLimitError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_limit_reached",
        ) from exc
    except WorkspaceMembershipError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_conflict",
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


@self_service_router.get("/me", response_model=MeMembershipsResponse)
def list_my_memberships(
    service: WorkspaceServiceDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> MeMembershipsResponse:
    """Return the memberships for the calling principal.

    Reaching this handler means the request is permitted: when backend
    authentication is disabled, ``authenticate_request`` returns an anonymous
    context, so the anonymous subject is used to query memberships.
    """
    pairs = service.memberships_for(user_id=auth.subject)
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
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> ActiveWorkspaceResponse:
    """Return the active workspace currently resolved for the request."""
    workspace = service.repository.get_workspace(context.workspace_id)
    email, user_name = extract_identity(auth.claims)
    if email is not None or user_name is not None:
        service.record_member_identity(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            email=email,
            user_name=user_name,
        )
    return ActiveWorkspaceResponse(
        workspace_id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        role=context.role,
    )


@router.get(
    "/{slug}/members",
    response_model=list[MembershipResponse],
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def list_workspace_members(
    slug: str,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> list[MembershipResponse]:
    """List members of the workspace."""
    try:
        workspace = service.repository.get_workspace_by_slug(slug)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    if context.workspace_id != workspace.id:
        raise_workspace_forbidden(
            "Cannot view members for a workspace you are not actively scoped to",
            error_code="workspace.scope_mismatch",
        )
    memberships = service.list_members(workspace.id)
    return [_to_membership_response(m) for m in memberships]


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
    except WorkspaceMembershipLimitError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_limit_reached",
        ) from exc
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


@router.get(
    "/{slug}/invitations",
    response_model=InvitationListResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def list_workspace_invitations(
    slug: str,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> InvitationListResponse:
    """List invitations for the workspace; requires admin or owner role."""
    workspace = _scoped_workspace(slug, service, context)
    invitations = service.list_invitations(workspace.id)
    return InvitationListResponse(
        invitations=[_to_invitation_response(i) for i in invitations]
    )


@router.post(
    "/{slug}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def create_workspace_invitation(
    slug: str,
    payload: InvitationCreateRequest,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> InvitationResponse:
    """Invite a user by email; sends an acceptance link to that address."""
    workspace = _scoped_workspace(slug, service, context)
    try:
        invitation = service.create_invitation(
            workspace_id=workspace.id,
            email=payload.email,
            role=payload.role,
            invited_by=auth.subject,
            actor_role=context.role,
            workspace_name=workspace.name,
        )
    except ValueError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=str(exc),
            error_code="workspace.invitation_invalid_email",
        ) from exc
    except WorkspaceInvitationError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.invitation_conflict",
        ) from exc
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))
    return _to_invitation_response(invitation)


@router.delete(
    "/{slug}/invitations/{invitation_id}",
    response_model=InvitationResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def revoke_workspace_invitation(
    slug: str,
    invitation_id: UUID,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> InvitationResponse:
    """Revoke a pending invitation; requires admin or owner role."""
    workspace = _scoped_workspace(slug, service, context)
    try:
        invitation = service.revoke_invitation(
            workspace_id=workspace.id,
            invitation_id=invitation_id,
            actor_role=context.role,
            actor=auth.subject,
        )
    except WorkspaceInvitationNotFoundError:
        raise_workspace_not_found("Invitation not found")
    except WorkspaceInvitationError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.invitation_conflict",
        ) from exc
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))
    return _to_invitation_response(invitation)


@self_service_router.post(
    "/invitations/accept",
    response_model=InvitationAcceptResponse,
)
def accept_workspace_invitation(
    payload: InvitationAcceptRequest,
    service: WorkspaceServiceDep,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> InvitationAcceptResponse:
    """Redeem an invitation token for the authenticated caller.

    Reachable by any logged-in user (no workspace scope required). The caller's
    verified email must match the invited address.
    """
    email, email_verified = _verified_email(auth)
    try:
        membership = service.accept_invitation(
            raw_token=payload.token,
            user_id=auth.subject,
            email=email,
            email_verified=email_verified,
        )
    except WorkspaceInvitationNotFoundError:
        raise_workspace_not_found("Invitation not found")
    except WorkspaceInvitationEmailMismatchError as exc:
        raise_workspace_forbidden(str(exc), error_code="workspace.invitation_email")
    except WorkspaceInvitationExpiredError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_410_GONE,
            message=str(exc),
            error_code="workspace.invitation_expired",
        ) from exc
    except WorkspaceMembershipLimitError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.membership_limit_reached",
        ) from exc
    except WorkspaceInvitationError as exc:
        raise WorkspaceHTTPError(
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
            error_code="workspace.invitation_conflict",
        ) from exc
    workspace = service.repository.get_workspace(membership.workspace_id)
    return InvitationAcceptResponse(
        workspace_id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        role=membership.role,
    )


def _scoped_workspace(
    slug: str,
    service: WorkspaceServiceDep,
    context: WorkspaceContextDep,
) -> Workspace:
    """Resolve a workspace by slug and assert the request is scoped to it."""
    try:
        workspace = service.repository.get_workspace_by_slug(slug)
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    if context.workspace_id != workspace.id:
        raise_workspace_forbidden(
            "Cannot manage invitations for a workspace you are not actively scoped to",
            error_code="workspace.scope_mismatch",
        )
    return workspace
