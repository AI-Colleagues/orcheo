"""High-level workspace management service used by API and CLI layers."""

from __future__ import annotations
import hashlib
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID
from orcheo.workspace.email import (
    InvitationEmail,
    InvitationEmailSender,
    LoggingInvitationEmailSender,
)
from orcheo.workspace.errors import (
    WorkspaceInvitationEmailMismatchError,
    WorkspaceInvitationError,
    WorkspaceInvitationExpiredError,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspacePermissionError,
)
from orcheo.workspace.models import (
    DEFAULT_WORKSPACE_SLUG,
    InvitationStatus,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_email,
    normalize_slug,
)
from orcheo.workspace.repository import WorkspaceRepository
from orcheo.workspace.resolver import WorkspaceResolver


__all__ = ["WorkspaceService", "ensure_default_workspace"]

MAX_WORKSPACE_MEMBERSHIPS_PER_USER = 3
DEFAULT_INVITATION_TTL_HOURS = 72
DEFAULT_INVITATION_BASE_URL = "http://localhost:2026"
_INVITATION_TOKEN_BYTES = 32


def _hash_invitation_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored in place of the raw token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def ensure_default_workspace(
    repository: WorkspaceRepository,
    *,
    slug: str = DEFAULT_WORKSPACE_SLUG,
    name: str = "Default Workspace",
) -> Workspace:
    """Return or create the default workspace."""
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
        email_sender: InvitationEmailSender | None = None,
        invitation_base_url: str = DEFAULT_INVITATION_BASE_URL,
        invitation_ttl_hours: int = DEFAULT_INVITATION_TTL_HOURS,
    ) -> None:
        """Bind the service to a repository and an optional resolver override."""
        self._repository = repository
        self._resolver = resolver or WorkspaceResolver(repository)
        self._email_sender = email_sender or LoggingInvitationEmailSender()
        self._invitation_base_url = invitation_base_url.rstrip("/")
        self._invitation_ttl_hours = invitation_ttl_hours

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
        owner_email: str | None = None,
        owner_name: str | None = None,
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
            email=owner_email,
            user_name=owner_name,
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

    def record_member_identity(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        email: str | None = None,
        user_name: str | None = None,
    ) -> None:
        """Best-effort backfill of a member's identity from their token claims.

        Called when an authenticated principal touches their active workspace so
        the membership row carries a human-readable email/name instead of only
        the opaque subject. Missing memberships and store errors are ignored.
        """
        if email is None and user_name is None:
            return
        try:
            self._repository.update_membership_identity(
                workspace_id,
                user_id,
                email=email,
                user_name=user_name,
            )
        except Exception:  # noqa: BLE001 - identity capture is best effort
            return

    def create_invitation(
        self,
        *,
        workspace_id: UUID,
        email: str,
        role: Role,
        invited_by: str | None = None,
        actor_role: Role | None = None,
        workspace_name: str | None = None,
    ) -> WorkspaceInvitation:
        """Create a pending invitation and deliver its acceptance email.

        Enforces admin+ access when ``actor_role`` is provided. A raw token is
        generated, persisted only as a hash, and embedded in the emailed link.
        """
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise WorkspacePermissionError("Only admins or owners can invite members")
        normalized_email = normalize_email(email)
        now = datetime.now(tz=UTC)
        raw_token = secrets.token_urlsafe(_INVITATION_TOKEN_BYTES)
        invitation = WorkspaceInvitation(
            workspace_id=workspace_id,
            email=normalized_email,
            role=role,
            token_hash=_hash_invitation_token(raw_token),
            invited_by=invited_by,
            created_at=now,
            expires_at=now + timedelta(hours=self._invitation_ttl_hours),
        )
        created = self._repository.add_invitation(invitation)
        self._record_invitation_audit(
            workspace_id,
            action="workspace.invitation.created",
            actor=invited_by,
            invitation=created,
            details={"role": role.value, "email": normalized_email},
        )
        self._send_invitation_email(
            created,
            raw_token=raw_token,
            workspace_name=workspace_name,
        )
        return created

    def list_invitations(
        self, workspace_id: UUID, *, include_inactive: bool = True
    ) -> list[WorkspaceInvitation]:
        """Return invitations for a workspace."""
        return self._repository.list_invitations(
            workspace_id, include_inactive=include_inactive
        )

    def revoke_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_role: Role | None = None,
        actor: str | None = None,
    ) -> WorkspaceInvitation:
        """Revoke a pending invitation; requires admin+ when actor_role is set."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise WorkspacePermissionError(
                "Only admins or owners can revoke invitations"
            )
        invitation = self._repository.get_invitation(invitation_id)
        if invitation.workspace_id != workspace_id:
            raise WorkspaceInvitationError(
                "Invitation does not belong to this workspace"
            )
        if invitation.status is not InvitationStatus.PENDING:
            raise WorkspaceInvitationError(
                f"Invitation is already {invitation.status.value}"
            )
        revoked = invitation.model_copy(update={"status": InvitationStatus.REVOKED})
        stored = self._repository.update_invitation(revoked)
        self._record_invitation_audit(
            workspace_id,
            action="workspace.invitation.revoked",
            actor=actor,
            invitation=stored,
        )
        return stored

    def accept_invitation(
        self,
        *,
        raw_token: str,
        user_id: str,
        email: str | None = None,
        email_verified: bool = False,
    ) -> WorkspaceMembership:
        """Redeem an invitation, binding it to the authenticated ``user_id``.

        The redeemer's verified email must match the invited address. This is the
        load-bearing control that stops a forwarded link being claimed by a
        stranger, since invitees may sign up with any (personal) email domain.
        """
        invitation = self._repository.get_invitation_by_token_hash(
            _hash_invitation_token(raw_token)
        )
        if invitation.status is InvitationStatus.REVOKED:
            raise WorkspaceInvitationError("Invitation has been revoked")
        if invitation.status is InvitationStatus.ACCEPTED:
            if invitation.accepted_by == user_id:
                return self._repository.get_membership(invitation.workspace_id, user_id)
            raise WorkspaceInvitationError("Invitation has already been accepted")
        if invitation.is_expired(now=datetime.now(tz=UTC)):
            raise WorkspaceInvitationExpiredError("Invitation has expired")

        normalized_email = normalize_email(email) if email else None
        if normalized_email is None:
            raise WorkspaceInvitationEmailMismatchError(
                "We couldn't read your email from your sign-in. Ensure your "
                "identity provider exposes a verified email, then open the "
                "invitation link again."
            )
        if not email_verified:
            raise WorkspaceInvitationEmailMismatchError(
                f"Your email ({normalized_email}) is not verified yet. Verify it "
                "with your identity provider, then open the invitation link again."
            )
        if normalized_email != invitation.email:
            raise WorkspaceInvitationEmailMismatchError(
                f"This invitation was sent to {invitation.email}, but you are "
                f"signed in as {normalized_email}."
            )

        try:
            self.invite_member(
                workspace_id=invitation.workspace_id,
                user_id=user_id,
                role=invitation.role,
            )
            membership = self._repository.update_membership_identity(
                invitation.workspace_id,
                user_id,
                email=invitation.email,
            )
        except WorkspaceMembershipLimitError:
            raise
        except WorkspaceMembershipError:
            # Already a member — treat acceptance as idempotent.
            membership = self._repository.get_membership(
                invitation.workspace_id, user_id
            )

        accepted = invitation.model_copy(
            update={
                "status": InvitationStatus.ACCEPTED,
                "accepted_by": user_id,
                "accepted_at": datetime.now(tz=UTC),
            }
        )
        self._repository.update_invitation(accepted)
        self._record_invitation_audit(
            invitation.workspace_id,
            action="workspace.invitation.accepted",
            actor=user_id,
            invitation=accepted,
        )
        self._resolver.invalidate(user_id)
        return membership

    def _send_invitation_email(
        self,
        invitation: WorkspaceInvitation,
        *,
        raw_token: str,
        workspace_name: str | None,
    ) -> None:
        if workspace_name is None:
            try:
                workspace_name = self._repository.get_workspace(
                    invitation.workspace_id
                ).name
            except Exception:  # noqa: BLE001 - fall back to a generic label
                workspace_name = "your workspace"
        accept_url = f"{self._invitation_base_url}/invitations/accept?token={raw_token}"
        self._email_sender.send_invitation(
            InvitationEmail(
                to=invitation.email,
                workspace_name=workspace_name,
                role=invitation.role.value,
                accept_url=accept_url,
                expires_at=invitation.expires_at,
                invited_by=invitation.invited_by,
            )
        )

    def _record_invitation_audit(
        self,
        workspace_id: UUID,
        *,
        action: str,
        actor: str | None,
        invitation: WorkspaceInvitation,
        details: dict[str, object] | None = None,
    ) -> None:
        try:
            self._repository.record_audit_event(
                WorkspaceAuditEvent(
                    workspace_id=workspace_id,
                    action=action,
                    actor=actor or "system",
                    subject=invitation.email,
                    resource_type="invitation",
                    resource_id=str(invitation.id),
                    details=details or {},
                )
            )
        except Exception:  # pragma: no cover - audit is best effort
            pass

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
