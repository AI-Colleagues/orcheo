"""Backend wiring for the multi-workspace subsystem."""

from orcheo_backend.app.workspace.dependencies import (
    WorkspaceContextDep,
    WorkspaceServiceDep,
    get_workspace_repository,
    get_workspace_resolver,
    get_workspace_service,
    require_role,
    require_workspace,
    reset_workspace_state,
    resolve_workspace_context,
    set_workspace_repository,
    set_workspace_service,
)
from orcheo_backend.app.workspace.errors import (
    WorkspaceContextRequiredError,
    WorkspaceHTTPError,
    raise_workspace_forbidden,
    raise_workspace_not_found,
)


__all__ = [
    "WorkspaceContextDep",
    "WorkspaceContextRequiredError",
    "WorkspaceHTTPError",
    "WorkspaceServiceDep",
    "get_workspace_repository",
    "get_workspace_resolver",
    "get_workspace_service",
    "raise_workspace_forbidden",
    "raise_workspace_not_found",
    "require_role",
    "require_workspace",
    "reset_workspace_state",
    "resolve_workspace_context",
    "set_workspace_repository",
    "set_workspace_service",
]
