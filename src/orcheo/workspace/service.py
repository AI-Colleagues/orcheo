"""High-level workspace management service used by API and CLI layers."""

from __future__ import annotations
from collections.abc import Iterable
from uuid import UUID
from orcheo.workspace.errors import (
    WorkspaceMembershipLimitError,
    WorkspacePermissionError,
)
from orcheo.workspace.models import (
    DEFAULT_WORKSPACE_SLUG,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_slug,
)
from orcheo.workspace.repository import WorkspaceRepository
from orcheo.workspace.resolver import WorkspaceResolver


__all__ = ["WorkspaceService", "ensure_default_workspace"]

MAX_WORKSPACE_MEMBERSHIPS_PER_USER = 3


def ensure_default_workspace(
    repository: WorkspaceRepository,
    *,
    slug: str = DEFAULT_WORKSPACE_SLUG,
    name: str = "Default Workspace",
) -> Workspace:
    """Return or create the legacy default workspace used by migrations."""
    try:
        return repository.get_workspace_by_slug(slug)
    except Exception:  # noqa: BLE001 - any "not found" surface is acceptable here
        workspace = Workspace(slug=slug, name=name)
        return repository.create_workspace(workspace)


class WorkspaceService:
    """Coordinates workspace CRUD and membership operations.

    Responsibilities:
    - Validate slugs and roles.
    - Cascade cache invalidation on membership changes.
    - Centralize role-based authorization checks for sensitive actions.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        resolver: WorkspaceResolver | None = None,
        *,
        default_workspace_slug: str | None = None,
    ) -> None:
        """Bind the service to a repository and an optional resolver override."""
        self._repository = repository
        self._resolver = resolver or WorkspaceResolver(
            repository, default_workspace_slug=default_workspace_slug
        )

    @property
    def repository(self) -> WorkspaceRepository:
        """Expose the underlying repository."""
        return self._repository

    @property
    def resolver(self) -> WorkspaceResolver:
        """Expose the bound workspace resolver."""
        return self._resolver

    def create_workspace(
        self,
        *,
        slug: str,
        name: str,
        owner_user_id: str,
        quotas: WorkspaceQuotas | None = None,
    ) -> tuple[Workspace, WorkspaceMembership]:
        """Create a workspace and assign `owner_user_id` as the owner."""
        workspace = Workspace(
            slug=normalize_slug(slug),
            name=name,
            quotas=quotas or WorkspaceQuotas(),
        )
        created = self._repository.create_workspace(workspace)
        membership = WorkspaceMembership(
            workspace_id=created.id,
            user_id=owner_user_id,
            role=Role.OWNER,
        )
        try:
            self._ensure_membership_capacity(owner_user_id)
            self._repository.add_membership(membership)
        except Exception:
            self._repository.delete_workspace(created.id)
            raise
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=created.id,
                    action="workspace.created",
                    actor=owner_user_id,
                    subject=owner_user_id,
                    resource_type="workspace",
                    resource_id=str(created.id),
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        self._resolver.invalidate(owner_user_id)
        return created, membership

    def list_workspaces(self, *, include_inactive: bool = False) -> list[Workspace]:
        """Return workspaces visible to operator-level callers."""
        return self._repository.list_workspaces(include_inactive=include_inactive)

    def deactivate_workspace(self, workspace_id: UUID) -> Workspace:
        """Mark a workspace as suspended; runs and APIs reject requests."""
        workspace = self._repository.update_status(
            workspace_id, WorkspaceStatus.SUSPENDED
        )
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace.id,
                    action="workspace.suspended",
                    actor="system",
                    resource_type="workspace",
                    resource_id=str(workspace.id),
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        return workspace

    def reactivate_workspace(self, workspace_id: UUID) -> Workspace:
        """Move a suspended workspace back to active."""
        workspace = self._repository.update_status(workspace_id, WorkspaceStatus.ACTIVE)
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace.id,
                    action="workspace.reactivated",
                    actor="system",
                    resource_type="workspace",
                    resource_id=str(workspace.id),
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        return workspace

    def soft_delete_workspace(self, workspace_id: UUID) -> Workspace:
        """Mark a workspace as deleted while preserving the row."""
        workspace = self._repository.update_status(
            workspace_id, WorkspaceStatus.DELETED
        )
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace.id,
                    action="workspace.deleted",
                    actor="system",
                    resource_type="workspace",
                    resource_id=str(workspace.id),
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        return workspace

    def hard_delete_workspace(self, workspace_id: UUID) -> None:
        """Remove a workspace and its memberships entirely."""
        memberships = self._repository.list_memberships_for_workspace(workspace_id)
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace_id,
                    action="workspace.purged",
                    actor="system",
                    resource_type="workspace",
                    resource_id=str(workspace_id),
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        self._repository.delete_workspace(workspace_id)
        for membership in memberships:
            self._resolver.invalidate(membership.user_id)

    def invite_member(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        role: Role,
        actor_role: Role | None = None,
    ) -> WorkspaceMembership:
        """Add a membership; if `actor_role` is given, enforces admin+ access."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise WorkspacePermissionError(
                "Only admins or owners can invite new members"
            )
        self._ensure_membership_capacity(user_id, workspace_id=workspace_id)
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
        )
        added = self._repository.add_membership(membership)
        self._resolver.invalidate(user_id)
        return added

    def remove_member(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        actor_role: Role | None = None,
    ) -> None:
        """Remove a membership; admin+ when `actor_role` is provided."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise WorkspacePermissionError("Only admins or owners can remove members")
        self._repository.remove_membership(workspace_id, user_id)
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace_id,
                    action="workspace.membership.removed",
                    actor=actor_role.value if actor_role is not None else "system",
                    subject=user_id,
                    resource_type="membership",
                    resource_id=user_id,
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        self._resolver.invalidate(user_id)

    def update_member_role(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        role: Role,
        actor_role: Role | None = None,
    ) -> WorkspaceMembership:
        """Change a member's role inside a workspace."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise WorkspacePermissionError(
                "Only admins or owners can change member roles"
            )
        updated = self._repository.update_membership_role(workspace_id, user_id, role)
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace_id,
                    action="workspace.membership.updated",
                    actor=actor_role.value if actor_role is not None else "system",
                    subject=user_id,
                    resource_type="membership",
                    resource_id=user_id,
                    details={"role": role.value},
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass
        self._resolver.invalidate(user_id)
        return updated

    def list_members(self, workspace_id: UUID) -> list[WorkspaceMembership]:
        """Return memberships for a workspace."""
        return self._repository.list_memberships_for_workspace(workspace_id)

    def memberships_for(
        self, user_id: str, *, workspaces: Iterable[Workspace] | None = None
    ) -> list[tuple[Workspace, WorkspaceMembership]]:
        """Return paired workspace/membership records for a user."""
        memberships = self._resolver.list_memberships(user_id)
        workspace_lookup: dict[UUID, Workspace] = (
            {workspace.id: workspace for workspace in workspaces}
            if workspaces is not None
            else {}
        )
        result: list[tuple[Workspace, WorkspaceMembership]] = []
        for membership in memberships:
            workspace = workspace_lookup.get(membership.workspace_id)
            if workspace is None:
                workspace = self._repository.get_workspace(membership.workspace_id)
            result.append((workspace, membership))
        return result

    def purge_deleted_workspaces(self, *, retention_days: int) -> list[Workspace]:
        """Hard-delete deleted workspaces older than the retention window."""
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
        purged: list[Workspace] = []
        for workspace in self._repository.list_workspaces(include_inactive=True):
            if workspace.status is not WorkspaceStatus.DELETED:
                continue
            if workspace.deleted_at is None or workspace.deleted_at > cutoff:
                continue
            self.hard_delete_workspace(workspace.id)
            purged.append(workspace)
        return purged

    def _ensure_membership_capacity(
        self, user_id: str, *, workspace_id: UUID | None = None
    ) -> None:
        """Raise when `user_id` already belongs to too many workspaces."""
        memberships = self._repository.list_memberships_for_user(user_id)
        if workspace_id is not None:
            for membership in memberships:
                if membership.workspace_id == workspace_id:
                    return
        if len(memberships) >= MAX_WORKSPACE_MEMBERSHIPS_PER_USER:
            raise WorkspaceMembershipLimitError(
                f"User {user_id} can belong to at most "
                f"{MAX_WORKSPACE_MEMBERSHIPS_PER_USER} workspaces"
            )
