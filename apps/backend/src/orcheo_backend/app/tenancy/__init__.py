"""Backend wiring for the multi-tenancy subsystem."""

from orcheo_backend.app.tenancy.dependencies import (
    TenantContextDep,
    TenantServiceDep,
    bootstrap_default_tenant,
    get_tenant_repository,
    get_tenant_resolver,
    get_tenant_service,
    require_role,
    require_tenant,
    reset_tenancy_state,
    resolve_tenant_context,
    set_tenant_repository,
    set_tenant_service,
)
from orcheo_backend.app.tenancy.errors import (
    TenantContextRequiredError,
    TenantHTTPError,
    raise_tenant_forbidden,
    raise_tenant_not_found,
)


__all__ = [
    "TenantContextDep",
    "TenantContextRequiredError",
    "TenantHTTPError",
    "TenantServiceDep",
    "bootstrap_default_tenant",
    "get_tenant_repository",
    "get_tenant_resolver",
    "get_tenant_service",
    "raise_tenant_forbidden",
    "raise_tenant_not_found",
    "require_role",
    "require_tenant",
    "reset_tenancy_state",
    "resolve_tenant_context",
    "set_tenant_repository",
    "set_tenant_service",
]
