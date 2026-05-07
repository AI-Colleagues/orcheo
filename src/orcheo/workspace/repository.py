"""Workspace repository protocol and an in-memory reference implementation."""

from __future__ import annotations
from typing import Protocol
from uuid import UUID
from orcheo.models.base import _utcnow
from orcheo.workspace.errors import (
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceStatus,
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


class InMemoryWorkspaceRepository:
    """In-memory workspace repository used for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._workspaces: dict[UUID, Workspace] = {}
        self._slug_index: dict[str, UUID] = {}
        self._memberships: dict[tuple[UUID, str], WorkspaceMembership] = {}
        self._audit_events: list[WorkspaceAuditEvent] = []

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
