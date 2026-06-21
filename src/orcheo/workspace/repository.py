"""Workspace repository protocol and an in-memory reference implementation."""

from __future__ import annotations
from typing import Protocol
from uuid import UUID
from orcheo.models.base import _utcnow
from orcheo.workspace.errors import (
    WorkspaceInvitationError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    InvitationStatus,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceStatus,
    normalize_email,
    normalize_slug,
)


__all__ = [
    "InMemoryWorkspaceRepository",
    "WorkspaceRepository",
]


class WorkspaceRepository(Protocol):
    """Storage protocol for workspaces and memberships."""

    def create_workspace(self, workspace: Workspace) -> Workspace:
        """Persist a new workspace; raises on slug conflict."""

    def get_workspace(self, workspace_id: UUID) -> Workspace:
        """Return the workspace identified by `workspace_id`."""

    def get_workspace_by_slug(self, slug: str) -> Workspace:
        """Return the workspace identified by `slug`."""

    def list_workspaces(self, *, include_inactive: bool = False) -> list[Workspace]:
        """List workspaces, optionally including suspended/deleted ones."""

    def update_status(self, workspace_id: UUID, status: WorkspaceStatus) -> Workspace:
        """Mutate the workspace's lifecycle status and return the updated record."""

    def delete_workspace(self, workspace_id: UUID) -> None:
        """Hard-delete a workspace and cascade its memberships."""

    def add_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        """Persist a new membership; raises on duplicates."""

    def remove_membership(self, workspace_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(workspace_id, user_id)`."""

    def update_membership_role(
        self, workspace_id: UUID, user_id: str, role: Role
    ) -> WorkspaceMembership:
        """Change a membership's role and return the updated record."""

    def update_membership_identity(
        self,
        workspace_id: UUID,
        user_id: str,
        *,
        email: str | None = None,
        user_name: str | None = None,
    ) -> WorkspaceMembership:
        """Backfill a membership's identity fields; ``None`` values are ignored."""

    def get_membership(self, workspace_id: UUID, user_id: str) -> WorkspaceMembership:
        """Return the membership identified by `(workspace_id, user_id)`."""

    def list_memberships_for_user(self, user_id: str) -> list[WorkspaceMembership]:
        """Return every membership for a given principal."""

    def list_memberships_for_workspace(
        self, workspace_id: UUID
    ) -> list[WorkspaceMembership]:
        """Return every membership inside a workspace."""

    def record_audit_event(self, event: WorkspaceAuditEvent) -> WorkspaceAuditEvent:
        """Persist a workspace audit event."""

    def list_audit_events(
        self, workspace_id: UUID, *, limit: int = 100
    ) -> list[WorkspaceAuditEvent]:
        """Return the most recent workspace audit events."""

    def add_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist a new invitation; raises on a duplicate pending email."""

    def get_invitation(self, invitation_id: UUID) -> WorkspaceInvitation:
        """Return the invitation identified by `invitation_id`."""

    def get_invitation_by_token_hash(self, token_hash: str) -> WorkspaceInvitation:
        """Return the invitation matching a token hash."""

    def find_pending_invitation(
        self, workspace_id: UUID, email: str
    ) -> WorkspaceInvitation | None:
        """Return the pending invitation for `(workspace_id, email)` if any."""

    def list_invitations(
        self, workspace_id: UUID, *, include_inactive: bool = True
    ) -> list[WorkspaceInvitation]:
        """Return invitations for a workspace, newest first."""

    def update_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist status/acceptance changes for an existing invitation."""


class InMemoryWorkspaceRepository:
    """In-memory workspace repository used for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._workspaces: dict[UUID, Workspace] = {}
        self._slug_index: dict[str, UUID] = {}
        self._memberships: dict[tuple[UUID, str], WorkspaceMembership] = {}
        self._audit_events: list[WorkspaceAuditEvent] = []
        self._invitations: dict[UUID, WorkspaceInvitation] = {}

    def create_workspace(self, workspace: Workspace) -> Workspace:
        """Persist a new workspace; raises on slug conflict."""
        slug = normalize_slug(workspace.slug)
        if slug in self._slug_index:
            msg = f"Workspace slug already exists: {slug}"
            raise WorkspaceSlugConflictError(msg)
        self._workspaces[workspace.id] = workspace
        self._slug_index[slug] = workspace.id
        return workspace

    def get_workspace(self, workspace_id: UUID) -> Workspace:
        """Return the workspace identified by `workspace_id`."""
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(str(workspace_id))
        return workspace

    def get_workspace_by_slug(self, slug: str) -> Workspace:
        """Return the workspace identified by `slug`."""
        normalized = normalize_slug(slug)
        workspace_id = self._slug_index.get(normalized)
        if workspace_id is None:
            raise WorkspaceNotFoundError(normalized)
        return self.get_workspace(workspace_id)

    def list_workspaces(self, *, include_inactive: bool = False) -> list[Workspace]:
        """List workspaces, optionally including suspended/deleted ones."""
        workspaces = list(self._workspaces.values())
        if include_inactive:
            return sorted(workspaces, key=lambda t: t.slug)
        return sorted(
            (t for t in workspaces if t.status is WorkspaceStatus.ACTIVE),
            key=lambda t: t.slug,
        )

    def update_status(self, workspace_id: UUID, status: WorkspaceStatus) -> Workspace:
        """Mutate the workspace's status and return the updated record."""
        workspace = self.get_workspace(workspace_id)
        workspace.status = status
        workspace.deleted_at = _utcnow() if status is WorkspaceStatus.DELETED else None
        return workspace

    def delete_workspace(self, workspace_id: UUID) -> None:
        """Hard-delete a workspace and cascade its memberships."""
        workspace = self.get_workspace(workspace_id)
        self._slug_index.pop(workspace.slug, None)
        self._workspaces.pop(workspace.id, None)
        for key in list(self._memberships):
            if key[0] == workspace.id:
                self._memberships.pop(key, None)
        self._audit_events = [
            event for event in self._audit_events if event.workspace_id != workspace.id
        ]
        for invitation_id in list(self._invitations):
            if self._invitations[invitation_id].workspace_id == workspace.id:
                self._invitations.pop(invitation_id, None)

    def add_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        """Persist a new membership; raises on duplicates."""
        if membership.workspace_id not in self._workspaces:
            raise WorkspaceNotFoundError(str(membership.workspace_id))
        key = (membership.workspace_id, membership.user_id)
        if key in self._memberships:
            msg = (
                f"Membership already exists for user {membership.user_id} in workspace "
                f"{membership.workspace_id}"
            )
            raise WorkspaceMembershipError(msg)
        self._memberships[key] = membership
        return membership

    def remove_membership(self, workspace_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(workspace_id, user_id)`."""
        key = (workspace_id, user_id)
        if key not in self._memberships:
            raise WorkspaceMembershipError(
                f"No membership for user {user_id} in workspace {workspace_id}"
            )
        self._memberships.pop(key, None)

    def update_membership_role(
        self, workspace_id: UUID, user_id: str, role: Role
    ) -> WorkspaceMembership:
        """Change a membership's role and return the updated record."""
        membership = self.get_membership(workspace_id, user_id)
        updated = membership.model_copy(update={"role": role})
        self._memberships[(workspace_id, user_id)] = updated
        return updated

    def update_membership_identity(
        self,
        workspace_id: UUID,
        user_id: str,
        *,
        email: str | None = None,
        user_name: str | None = None,
    ) -> WorkspaceMembership:
        """Backfill a membership's identity fields; ``None`` values are ignored."""
        membership = self.get_membership(workspace_id, user_id)
        updates: dict[str, str] = {}
        if email is not None:
            updates["email"] = email
        if user_name is not None:
            updates["user_name"] = user_name
        if not updates:
            return membership
        updated = membership.model_copy(update=updates)
        self._memberships[(workspace_id, user_id)] = updated
        return updated

    def get_membership(self, workspace_id: UUID, user_id: str) -> WorkspaceMembership:
        """Return the membership identified by `(workspace_id, user_id)`."""
        key = (workspace_id, user_id)
        membership = self._memberships.get(key)
        if membership is None:
            raise WorkspaceMembershipError(
                f"No membership for user {user_id} in workspace {workspace_id}"
            )
        return membership

    def list_memberships_for_user(self, user_id: str) -> list[WorkspaceMembership]:
        """Return every membership for a given principal."""
        return [m for m in self._memberships.values() if m.user_id == user_id]

    def list_memberships_for_workspace(
        self, workspace_id: UUID
    ) -> list[WorkspaceMembership]:
        """Return every membership inside a workspace."""
        return [m for m in self._memberships.values() if m.workspace_id == workspace_id]

    def record_audit_event(self, event: WorkspaceAuditEvent) -> WorkspaceAuditEvent:
        """Persist a workspace audit event."""
        self._audit_events.append(event)
        return event

    def list_audit_events(
        self, workspace_id: UUID, *, limit: int = 100
    ) -> list[WorkspaceAuditEvent]:
        """Return the most recent workspace audit events."""
        events = [
            event for event in self._audit_events if event.workspace_id == workspace_id
        ]
        return list(reversed(events[-limit:]))

    def add_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist a new invitation; raises on a duplicate pending email."""
        if invitation.workspace_id not in self._workspaces:
            raise WorkspaceNotFoundError(str(invitation.workspace_id))
        if invitation.status is InvitationStatus.PENDING:
            existing = self.find_pending_invitation(
                invitation.workspace_id, invitation.email
            )
            if existing is not None:
                raise WorkspaceInvitationError(
                    f"A pending invitation already exists for {invitation.email}"
                )
        self._invitations[invitation.id] = invitation
        return invitation

    def get_invitation(self, invitation_id: UUID) -> WorkspaceInvitation:
        """Return the invitation identified by `invitation_id`."""
        invitation = self._invitations.get(invitation_id)
        if invitation is None:
            raise WorkspaceInvitationNotFoundError(str(invitation_id))
        return invitation

    def get_invitation_by_token_hash(self, token_hash: str) -> WorkspaceInvitation:
        """Return the invitation matching a token hash."""
        for invitation in self._invitations.values():
            if invitation.token_hash == token_hash:
                return invitation
        raise WorkspaceInvitationNotFoundError(token_hash)

    def find_pending_invitation(
        self, workspace_id: UUID, email: str
    ) -> WorkspaceInvitation | None:
        """Return the pending invitation for `(workspace_id, email)` if any."""
        normalized = normalize_email(email)
        for invitation in self._invitations.values():
            if (
                invitation.workspace_id == workspace_id
                and invitation.email == normalized
                and invitation.status is InvitationStatus.PENDING
            ):
                return invitation
        return None

    def list_invitations(
        self, workspace_id: UUID, *, include_inactive: bool = True
    ) -> list[WorkspaceInvitation]:
        """Return invitations for a workspace, newest first."""
        invitations = [
            invitation
            for invitation in self._invitations.values()
            if invitation.workspace_id == workspace_id
            and (include_inactive or invitation.status is InvitationStatus.PENDING)
        ]
        return sorted(invitations, key=lambda i: i.created_at, reverse=True)

    def update_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist status/acceptance changes for an existing invitation."""
        if invitation.id not in self._invitations:
            raise WorkspaceInvitationNotFoundError(str(invitation.id))
        self._invitations[invitation.id] = invitation
        return invitation
