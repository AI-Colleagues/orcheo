"""Tenancy core: identity, repositories, and resolver for multi-tenant Orcheo."""

from orcheo.tenancy.errors import (
    TenantError,
    TenantMembershipError,
    TenantNotFoundError,
    TenantPermissionError,
    TenantSlugConflictError,
)
from orcheo.tenancy.migrations import (
    TENANT_ID_BACKFILL_TABLES,
    add_tenant_id_column_sqlite,
    backfill_tenant_id_sqlite,
    ensure_default_tenant_for_repository,
    ensure_tenant_index_sqlite,
    run_sqlite_backfill,
)
from orcheo.tenancy.models import (
    DEFAULT_TENANT_SLUG,
    Role,
    Tenant,
    TenantContext,
    TenantMembership,
    TenantQuotas,
    TenantStatus,
    normalize_slug,
)
from orcheo.tenancy.postgres_schema import POSTGRES_TENANT_SCHEMA
from orcheo.tenancy.repository import InMemoryTenantRepository, TenantRepository
from orcheo.tenancy.resolver import (
    InMemoryMembershipCache,
    MembershipCache,
    TenantResolver,
)
from orcheo.tenancy.scoping import (
    TenantScopeError,
    coerce_tenant_id,
    ensure_tenant_id,
    tenant_scoped_sql,
)
from orcheo.tenancy.service import TenantService, ensure_default_tenant
from orcheo.tenancy.sqlite_store import (
    SQLITE_TENANT_SCHEMA_SQL,
    SqliteTenantRepository,
    ensure_tenant_schema,
)


__all__ = [
    "DEFAULT_TENANT_SLUG",
    "TENANT_ID_BACKFILL_TABLES",
    "add_tenant_id_column_sqlite",
    "backfill_tenant_id_sqlite",
    "ensure_default_tenant_for_repository",
    "ensure_tenant_index_sqlite",
    "run_sqlite_backfill",
    "InMemoryMembershipCache",
    "InMemoryTenantRepository",
    "MembershipCache",
    "POSTGRES_TENANT_SCHEMA",
    "Role",
    "SQLITE_TENANT_SCHEMA_SQL",
    "SqliteTenantRepository",
    "Tenant",
    "TenantContext",
    "TenantError",
    "TenantMembership",
    "TenantMembershipError",
    "TenantNotFoundError",
    "TenantPermissionError",
    "TenantQuotas",
    "TenantRepository",
    "TenantResolver",
    "TenantScopeError",
    "TenantService",
    "TenantSlugConflictError",
    "TenantStatus",
    "coerce_tenant_id",
    "ensure_default_tenant",
    "ensure_tenant_id",
    "ensure_tenant_schema",
    "normalize_slug",
    "tenant_scoped_sql",
]
