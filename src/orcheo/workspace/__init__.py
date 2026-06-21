"""Workspace core: identity, repositories, and resolver for multi-workspace Orcheo."""

from orcheo.workspace.email import (
    DEFAULT_INVITE_FROM_EMAIL,
    InvitationEmail,
    InvitationEmailSender,
    LoggingInvitationEmailSender,
    ResendInvitationEmailSender,
    build_invitation_email_sender,
)
from orcheo.workspace.errors import (
    WorkspaceError,
    WorkspaceInvitationEmailMismatchError,
    WorkspaceInvitationError,
    WorkspaceInvitationExpiredError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    DEFAULT_WORKSPACE_SLUG,
    InvitationStatus,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceContext,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_email,
    normalize_slug,
)
from orcheo.workspace.postgres_schema import POSTGRES_WORKSPACE_SCHEMA
from orcheo.workspace.postgres_store import PostgresWorkspaceRepository
from orcheo.workspace.repository import InMemoryWorkspaceRepository, WorkspaceRepository
from orcheo.workspace.resolver import (
    InMemoryMembershipCache,
    MembershipCache,
    WorkspaceResolver,
)
from orcheo.workspace.scoping import (
    WorkspaceScopeError,
    coerce_workspace_id,
    ensure_workspace_id,
    workspace_scoped_sql,
)
from orcheo.workspace.service import WorkspaceService, ensure_default_workspace


__all__ = [
    "DEFAULT_WORKSPACE_SLUG",
    "DEFAULT_INVITE_FROM_EMAIL",
    "InMemoryMembershipCache",
    "InMemoryWorkspaceRepository",
    "InvitationEmail",
    "InvitationEmailSender",
    "InvitationStatus",
    "LoggingInvitationEmailSender",
    "ResendInvitationEmailSender",
    "build_invitation_email_sender",
    "MembershipCache",
    "POSTGRES_WORKSPACE_SCHEMA",
    "PostgresWorkspaceRepository",
    "Role",
    "WorkspaceAuditEvent",
    "Workspace",
    "WorkspaceContext",
    "WorkspaceError",
    "WorkspaceInvitation",
    "WorkspaceInvitationEmailMismatchError",
    "WorkspaceInvitationError",
    "WorkspaceInvitationExpiredError",
    "WorkspaceInvitationNotFoundError",
    "WorkspaceMembership",
    "WorkspaceMembershipError",
    "WorkspaceMembershipLimitError",
    "WorkspaceNotFoundError",
    "WorkspacePermissionError",
    "WorkspaceQuotas",
    "WorkspaceRepository",
    "WorkspaceResolver",
    "WorkspaceScopeError",
    "WorkspaceService",
    "WorkspaceSlugConflictError",
    "WorkspaceStatus",
    "coerce_workspace_id",
    "ensure_default_workspace",
    "ensure_workspace_id",
    "normalize_email",
    "normalize_slug",
    "workspace_scoped_sql",
]
