"""Workspace core: identity, repositories, and resolver for multi-workspace Orcheo."""

from orcheo.workspace.errors import (
    WorkspaceError,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.migrations import (
    WORKSPACE_ID_BACKFILL_TABLES,
    add_workspace_id_column_sqlite,
    backfill_workspace_id_sqlite,
    ensure_default_workspace_for_repository,
    ensure_workspace_index_sqlite,
    run_sqlite_backfill,
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
from orcheo.workspace.sqlite_store import (
    SQLITE_WORKSPACE_SCHEMA_SQL,
    SqliteWorkspaceRepository,
    ensure_workspace_schema,
)


__all__ = [
    "DEFAULT_WORKSPACE_SLUG",
    "WORKSPACE_ID_BACKFILL_TABLES",
    "add_workspace_id_column_sqlite",
    "backfill_workspace_id_sqlite",
    "ensure_default_workspace_for_repository",
    "ensure_workspace_index_sqlite",
    "run_sqlite_backfill",
    "InMemoryMembershipCache",
    "InMemoryWorkspaceRepository",
    "MembershipCache",
    "POSTGRES_WORKSPACE_SCHEMA",
    "PostgresWorkspaceRepository",
    "Role",
    "SQLITE_WORKSPACE_SCHEMA_SQL",
    "SqliteWorkspaceRepository",
    "WorkspaceAuditEvent",
    "Workspace",
    "WorkspaceContext",
    "WorkspaceError",
    "WorkspaceMembership",
    "WorkspaceMembershipError",
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
    "ensure_workspace_schema",
    "normalize_slug",
    "workspace_scoped_sql",
]
