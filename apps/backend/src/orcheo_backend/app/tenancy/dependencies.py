"""FastAPI dependencies for tenant resolution and role enforcement."""

from __future__ import annotations
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from fastapi import Depends, Request
from orcheo.config import get_settings
from orcheo.tenancy import (
    DEFAULT_TENANT_SLUG,
    InMemoryTenantRepository,
    Role,
    Tenant,
    TenantContext,
    TenantMembership,
    TenantMembershipError,
    TenantNotFoundError,
    TenantPermissionError,
    TenantRepository,
    TenantResolver,
    TenantService,
    ensure_default_tenant,
)
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.errors import TenantRateLimitError
from orcheo_backend.app.tenancy.errors import (
    TenantContextRequiredError,
    raise_tenant_forbidden,
    raise_tenant_not_found,
)
from orcheo_backend.app.tenant_governance import get_tenant_governance


__all__ = [
    "TenantContextDep",
    "TenantServiceDep",
    "bootstrap_default_tenant",
    "get_tenant_repository",
    "get_tenant_resolver",
    "get_tenant_service",
    "require_role",
    "require_tenant",
    "resolve_tenant_context",
    "reset_tenancy_state",
    "set_tenant_repository",
    "set_tenant_service",
]


_tenant_repository_ref: dict[str, TenantRepository | None] = {"repository": None}
_tenant_service_ref: dict[str, TenantService | None] = {"service": None}


def set_tenant_repository(repository: TenantRepository | None) -> None:
    """Override the tenant repository singleton (primarily for testing)."""
    _tenant_repository_ref["repository"] = repository
    _tenant_service_ref["service"] = None


def set_tenant_service(service: TenantService | None) -> None:
    """Override the tenant service singleton (primarily for testing)."""
    _tenant_service_ref["service"] = service
    if service is not None:
        _tenant_repository_ref["repository"] = service.repository


def reset_tenancy_state() -> None:
    """Drop cached tenancy singletons; refreshes settings."""
    _tenant_repository_ref["repository"] = None
    _tenant_service_ref["service"] = None
    get_settings(refresh=True)
    from orcheo_backend.app.tenant_governance import get_tenant_governance

    get_tenant_governance(refresh=True)


def get_tenant_repository() -> TenantRepository:
    """Return the configured tenant repository, falling back to in-memory."""
    repository = _tenant_repository_ref.get("repository")
    if repository is None:
        repository = InMemoryTenantRepository()
        _tenant_repository_ref["repository"] = repository
    return repository


def get_tenant_service() -> TenantService:
    """Return the cached tenant service singleton."""
    service = _tenant_service_ref.get("service")
    if service is None:
        service = TenantService(get_tenant_repository())
        _tenant_service_ref["service"] = service
    return service


def get_tenant_resolver() -> TenantResolver:
    """Return the resolver bound to the current service."""
    return get_tenant_service().resolver


def bootstrap_default_tenant(
    *,
    user_id: str | None = None,
    repository: TenantRepository | None = None,
) -> Tenant:
    """Ensure the default tenant exists and (optionally) the principal is in it.

    Used by the foundation rollout: with `multi_tenancy.enabled=False`, every
    request resolves to this default tenant. If `user_id` is supplied and has
    no membership, an owner membership is created.
    """
    repo = repository if repository is not None else get_tenant_repository()
    settings = get_settings()
    default_slug = str(
        settings.get("MULTI_TENANCY_DEFAULT_TENANT_SLUG", DEFAULT_TENANT_SLUG)
    )
    tenant = ensure_default_tenant(repo, slug=default_slug)
    if user_id is None:
        return tenant
    try:
        repo.get_membership(tenant.id, user_id)
    except TenantMembershipError:
        repo.add_membership(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user_id,
                role=Role.OWNER,
            )
        )
    return tenant


def _read_tenant_header(request: Request) -> str | None:
    settings = get_settings()
    header_name = str(settings.get("MULTI_TENANCY_TENANT_HEADER", "X-Orcheo-Tenant"))
    raw = request.headers.get(header_name)
    if raw is None:
        return None
    candidate = raw.strip()
    return candidate or None


async def resolve_tenant_context(
    request: Request,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
) -> TenantContext:
    """FastAPI dependency that produces a TenantContext for the request.

    Behavior depends on `MULTI_TENANCY_ENABLED`:
    - When False: every request resolves to the default tenant; the principal
      (or an anonymous sentinel when auth is disabled) is auto-enrolled as
      owner if missing, preserving single-tenant compatibility.
    - When True: the principal must be authenticated and have a membership;
      an explicit slug header pins the active tenant when the user has
      multiple memberships.
    """
    settings = get_settings()
    enabled = bool(settings.get("MULTI_TENANCY_ENABLED", False))

    if not auth.is_authenticated:
        if enabled:
            raise TenantContextRequiredError("Authentication is required for tenancy")
        user_id = auth.subject or "anonymous"
    else:
        user_id = auth.subject

    service = get_tenant_service()
    requested_slug = _read_tenant_header(request)

    if not enabled:
        bootstrap_default_tenant(user_id=user_id)

    try:
        context = service.resolver.resolve(
            user_id=user_id,
            tenant_slug=requested_slug,
        )
    except TenantNotFoundError:
        raise_tenant_not_found()
    except TenantPermissionError as exc:
        raise_tenant_forbidden(str(exc))
    except TenantMembershipError as exc:
        raise_tenant_forbidden(str(exc), error_code="tenant.membership_required")

    try:
        get_tenant_governance().check_api_rate_limit(str(context.tenant_id))
    except TenantRateLimitError as exc:
        raise exc.as_http_exception() from exc
    request.state.tenant = context
    return context


TenantContextDep = Annotated[TenantContext, Depends(resolve_tenant_context)]
TenantServiceDep = Annotated[TenantService, Depends(get_tenant_service)]


async def require_tenant(
    context: TenantContextDep,
) -> TenantContext:
    """Require that the request has resolved tenant context.

    Mirrors the `require_tenant()` helper described in the design doc; useful
    when a route needs only the resolved context without an explicit role.
    """
    return context


def require_role(
    role: Role,
) -> Callable[[Request, TenantContext], Coroutine[Any, Any, TenantContext]]:
    """Build a FastAPI dependency that enforces a minimum tenant role."""

    async def _checker(
        request: Request,
        context: TenantContextDep,
    ) -> TenantContext:
        if not context.has_role(role):
            raise_tenant_forbidden(
                f"Role '{role.value}' or higher is required",
                error_code="tenant.role_required",
            )
        return context

    return _checker
