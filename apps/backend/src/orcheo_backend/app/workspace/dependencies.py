"""FastAPI dependencies for workspace resolution and role enforcement."""

from __future__ import annotations
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from fastapi import Depends, Request
from orcheo.config import get_settings
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    PostgresWorkspaceRepository,
    Role,
    SqliteWorkspaceRepository,
    WorkspaceContext,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceRepository,
    WorkspaceResolver,
    WorkspaceService,
)
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.errors import WorkspaceRateLimitError
from orcheo_backend.app.workspace.errors import (
    WorkspaceContextRequiredError,
    raise_workspace_forbidden,
    raise_workspace_not_found,
)
from orcheo_backend.app.workspace_governance import get_workspace_governance


__all__ = [
    "WorkspaceContextDep",
    "WorkspaceServiceDep",
    "get_workspace_repository",
    "get_workspace_resolver",
    "get_workspace_service",
    "require_role",
    "require_workspace",
    "resolve_workspace_context",
    "reset_workspace_state",
    "set_workspace_repository",
    "set_workspace_service",
]


_workspace_repository_ref: dict[str, WorkspaceRepository | None] = {"repository": None}
_workspace_service_ref: dict[str, WorkspaceService | None] = {"service": None}


def set_workspace_repository(repository: WorkspaceRepository | None) -> None:
    """Override the workspace repository singleton (primarily for testing)."""
    _workspace_repository_ref["repository"] = repository
    _workspace_service_ref["service"] = None


def set_workspace_service(service: WorkspaceService | None) -> None:
    """Override the workspace service singleton (primarily for testing)."""
    _workspace_service_ref["service"] = service
    if service is not None:
        _workspace_repository_ref["repository"] = service.repository


def reset_workspace_state() -> None:
    """Drop cached workspace singletons; refreshes settings."""
    _workspace_repository_ref["repository"] = None
    _workspace_service_ref["service"] = None
    get_settings(refresh=True)
    from orcheo_backend.app.workspace_governance import get_workspace_governance

    get_workspace_governance(refresh=True)


def get_workspace_repository() -> WorkspaceRepository:
    """Return the configured workspace repository, falling back to in-memory."""
    repository = _workspace_repository_ref.get("repository")
    if repository is None:
        settings = get_settings()
        backend = str(settings.get("WORKSPACE_BACKEND", "inmemory")).lower()
        if backend == "postgres":
            dsn = settings.get("POSTGRES_DSN")
            if not dsn:
                msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
                raise ValueError(msg)
            repository = PostgresWorkspaceRepository(str(dsn))
        elif backend == "sqlite":
            repository = SqliteWorkspaceRepository(
                str(
                    settings.get("WORKSPACE_SQLITE_PATH", "~/.orcheo/workspaces.sqlite")
                )
            )
        else:
            repository = InMemoryWorkspaceRepository()
        _workspace_repository_ref["repository"] = repository
    return repository


def get_workspace_service() -> WorkspaceService:
    """Return the cached workspace service singleton."""
    service = _workspace_service_ref.get("service")
    if service is None:
        service = WorkspaceService(get_workspace_repository())
        _workspace_service_ref["service"] = service
    return service


def get_workspace_resolver() -> WorkspaceResolver:
    """Return the resolver bound to the current service."""
    return get_workspace_service().resolver


def _read_workspace_header(request: Request) -> str | None:
    settings = get_settings()
    header_name = str(
        settings.get("MULTI_WORKSPACE_WORKSPACE_HEADER", "X-Orcheo-Workspace")
    )
    raw = request.headers.get(header_name)
    if raw is None:
        return None
    candidate = raw.strip()
    return candidate or None


async def resolve_workspace_context(
    request: Request,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> WorkspaceContext:
    """FastAPI dependency that produces a WorkspaceContext for the request.

    The caller must be authenticated and have at least one workspace membership.
    An explicit slug header pins the active workspace when the user has multiple
    memberships.
    """
    if not auth.is_authenticated:
        raise WorkspaceContextRequiredError("Authentication is required for workspace")

    service = get_workspace_service()
    requested_slug = _read_workspace_header(request)

    try:
        context = service.resolver.resolve(
            user_id=auth.subject,
            workspace_slug=requested_slug,
        )
    except WorkspaceNotFoundError:
        raise_workspace_not_found()
    except WorkspacePermissionError as exc:
        raise_workspace_forbidden(str(exc))
    except WorkspaceMembershipError as exc:
        raise_workspace_forbidden(str(exc), error_code="workspace.membership_required")

    try:
        get_workspace_governance().check_api_rate_limit(str(context.workspace_id))
    except WorkspaceRateLimitError as exc:
        raise exc.as_http_exception() from exc
    request.state.workspace = context
    return context


WorkspaceContextDep = Annotated[WorkspaceContext, Depends(resolve_workspace_context)]
WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]


async def require_workspace(
    context: WorkspaceContextDep,
) -> WorkspaceContext:
    """Require that the request has resolved workspace context.

    Mirrors the `require_workspace()` helper described in the design doc; useful
    when a route needs only the resolved context without an explicit role.
    """
    return context


def require_role(
    role: Role,
) -> Callable[[Request, WorkspaceContext], Coroutine[Any, Any, WorkspaceContext]]:
    """Build a FastAPI dependency that enforces a minimum workspace role."""

    async def _checker(
        request: Request,
        context: WorkspaceContextDep,
    ) -> WorkspaceContext:
        if not context.has_role(role):
            raise_workspace_forbidden(
                f"Role '{role.value}' or higher is required",
                error_code="workspace.role_required",
            )
        return context

    return _checker
