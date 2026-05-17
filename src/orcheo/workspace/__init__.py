"""Workspace core: identity, repositories, and resolver for multi-workspace Orcheo."""

from orcheo.workspace.errors import (
    WorkspaceError,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    DEFAULT_WORKSPACE_SLUG,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceContext,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
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
    "InMemoryMembershipCache",
    "InMemoryWorkspaceRepository",
    "MembershipCache",
    "POSTGRES_WORKSPACE_SCHEMA",
    "PostgresWorkspaceRepository",
    "Role",
    "WorkspaceAuditEvent",
    "Workspace",
    "WorkspaceContext",
    "WorkspaceError",
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
    "normalize_slug",
    "workspace_scoped_sql",
]
