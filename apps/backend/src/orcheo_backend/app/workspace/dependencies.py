"""FastAPI dependencies for workspace resolution and role enforcement."""

from __future__ import annotations
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID
from fastapi import Depends, Request
from orcheo.config import get_settings
from orcheo.workspace import (
    PostgresWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceContext,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceRepository,
    WorkspaceResolver,
    WorkspaceService,
    WorkspaceStatus,
)
from orcheo.workspace.email import build_invitation_email_sender
from orcheo.workspace.service import (
    DEFAULT_INVITATION_BASE_URL,
    DEFAULT_INVITATION_TTL_HOURS,
)
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.errors import WorkspaceRateLimitError
from orcheo_backend.app.workspace.errors import (
    raise_workspace_forbidden,
    raise_workspace_not_found,
    raise_workspace_required,
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
    "_resolve_from_authorized_workspaces",
]


WORKSPACE_HEADER = "X-Orcheo-Workspace"

_workspace_repository_ref: dict[str, WorkspaceRepository | None] = {"repository": None}
_workspace_service_ref: dict[str, WorkspaceService | None] = {"service": None}


def set_workspace_repository(repository: WorkspaceRepository | None) -> None:
    """Override the workspace repository singleton (primarily for testing)."""
    _workspace_repository_ref["repository"] = repository
    _workspace_service_ref["service"] = None


def set_workspace_service(service: WorkspaceService | None) -> None:
    """Override the workspace service singleton (primarily for testing)."""
    _workspace_service_ref["service"] = service
    if service is not None:  # pragma: no branch
        _workspace_repository_ref["repository"] = service.repository


def reset_workspace_state() -> None:
    """Drop cached workspace singletons; refreshes settings."""
    _workspace_repository_ref["repository"] = None
    _workspace_service_ref["service"] = None
    get_settings(refresh=True)
    from orcheo_backend.app.workspace_governance import get_workspace_governance

    get_workspace_governance(refresh=True)


def get_workspace_repository() -> WorkspaceRepository:
    """Return the configured workspace repository."""
    repository = _workspace_repository_ref.get("repository")
    if repository is None:
        settings = get_settings()
        backend = str(settings.get("WORKSPACE_BACKEND", "postgres")).lower()
        if backend != "postgres":
            msg = "ORCHEO_WORKSPACE_BACKEND must be 'postgres'."
            raise ValueError(msg)
        dsn = settings.get("POSTGRES_DSN")
        if not dsn:
            msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
            raise ValueError(msg)
        repository = PostgresWorkspaceRepository(str(dsn))
        _workspace_repository_ref["repository"] = repository
    return repository


def get_workspace_service() -> WorkspaceService:
    """Return the cached workspace service singleton."""
    service = _workspace_service_ref.get("service")
    if service is None:
        settings = get_settings()
        base_url = str(settings.get("STUDIO_URL") or DEFAULT_INVITATION_BASE_URL)
        ttl_hours = int(
            settings.get("INVITE_TTL_HOURS") or DEFAULT_INVITATION_TTL_HOURS
        )
        email_sender = build_invitation_email_sender(
            api_key=settings.get("RESEND_API_KEY"),
            from_email=settings.get("INVITE_FROM_EMAIL"),
        )
        service = WorkspaceService(
            get_workspace_repository(),
            email_sender=email_sender,
            invitation_base_url=base_url,
            invitation_ttl_hours=ttl_hours,
        )
        _workspace_service_ref["service"] = service
    return service


def get_workspace_resolver() -> WorkspaceResolver:
    """Return the resolver bound to the current service."""
    return get_workspace_service().resolver


def _read_workspace_header(request: Request) -> str | None:
    raw = request.headers.get(WORKSPACE_HEADER)
    if raw is None:
        return None
    candidate = raw.strip()
    return candidate or None


async def resolve_workspace_context(
    request: Request,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> WorkspaceContext:
    """FastAPI dependency that produces a WorkspaceContext for the request.

    Service tokens and dev logins that carry *workspace_ids* in their claims
    are resolved directly from those identifiers; user identities are resolved
    via the membership-based resolver. When authentication is disabled,
    ``authenticate_request`` yields an anonymous context that resolves via the
    membership-based path using ``anonymous`` as the subject.
    """
    service = get_workspace_service()
    requested_slug = _read_workspace_header(request)

    identity_type = getattr(auth, "identity_type", "service")
    if auth.workspace_ids and identity_type != "user":
        context = _resolve_from_authorized_workspaces(
            repository=service.repository,
            workspace_ids=auth.workspace_ids,
            requested_slug=requested_slug,
        )
    else:
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
            raise_workspace_forbidden(
                str(exc), error_code="workspace.membership_required"
            )

    try:
        get_workspace_governance().check_api_rate_limit(str(context.workspace_id))
    except WorkspaceRateLimitError as exc:
        raise exc.as_http_exception() from exc
    request.state.workspace = context
    return context


def _resolve_from_authorized_workspaces(
    *,
    repository: WorkspaceRepository,
    workspace_ids: frozenset[str],
    requested_slug: str | None = None,
) -> WorkspaceContext:
    """Resolve a workspace context from token-authorized workspace IDs.

    Service tokens and dev logins carry *workspace_ids* directly in their
    claims.  This path resolves the workspace from those IDs without requiring
    a user-membership row (which does not exist for token identifiers).
    """
    workspaces: list[Workspace] = []
    for wid in workspace_ids:
        try:
            workspace = repository.get_workspace(UUID(wid))
            workspaces.append(workspace)
        except WorkspaceNotFoundError:
            continue

    if not workspaces:
        raise_workspace_forbidden(
            "No authorized workspaces found",
            error_code="workspace.membership_required",
        )

    if requested_slug is not None:
        for workspace in workspaces:
            if workspace.slug == requested_slug:
                selected = workspace
                break
        else:
            raise_workspace_not_found()
    elif len(workspaces) == 1:
        selected = workspaces[0]
    else:
        raise_workspace_required(
            "Workspace header is required when multiple workspaces are authorized"
        )

    if selected.status is not WorkspaceStatus.ACTIVE:
        raise_workspace_forbidden(
            f"Workspace {selected.slug} is not active (status={selected.status.value})"
        )

    return WorkspaceContext(
        workspace_id=selected.id,
        workspace_slug=selected.slug,
        user_id="service",
        role=Role.OWNER,
        quotas=selected.quotas,
    )


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
