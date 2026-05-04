"""Unit tests for tenancy domain models."""

from __future__ import annotations
import pytest
from orcheo.tenancy import (
    Role,
    Tenant,
    TenantContext,
    TenantQuotas,
    TenantStatus,
    normalize_slug,
)


def test_role_includes_uses_rank_hierarchy() -> None:
    assert Role.OWNER.includes(Role.VIEWER)
    assert Role.OWNER.includes(Role.OWNER)
    assert Role.ADMIN.includes(Role.EDITOR)
    assert not Role.EDITOR.includes(Role.ADMIN)
    assert not Role.VIEWER.includes(Role.EDITOR)


def test_normalize_slug_lowercases_and_validates() -> None:
    assert normalize_slug("Acme") == "acme"
    assert normalize_slug("acme-prod_1") == "acme-prod_1"
    with pytest.raises(ValueError, match="empty"):
        normalize_slug("   ")
    with pytest.raises(ValueError, match="alphanumeric"):
        normalize_slug("acme inc")


def test_tenant_validates_slug_and_name() -> None:
    tenant = Tenant(slug="Acme", name="Acme Inc")
    assert tenant.slug == "acme"
    assert tenant.status is TenantStatus.ACTIVE
    assert tenant.quotas == TenantQuotas()
    with pytest.raises(ValueError):
        Tenant(slug="", name="x")
    with pytest.raises(ValueError):
        Tenant(slug="ok", name="   ")


def test_tenant_quotas_defaults_are_positive() -> None:
    quotas = TenantQuotas()
    assert quotas.max_workflows >= 1
    assert quotas.max_concurrent_runs >= 1
    assert quotas.max_credentials >= 1
    assert quotas.max_storage_rows >= 1


def test_tenant_context_round_trips_through_headers() -> None:
    tenant = Tenant(slug="Acme", name="Acme Inc")
    ctx = TenantContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        user_id="alice",
        role=Role.ADMIN,
        quotas=tenant.quotas,
    )
    headers = ctx.to_headers()
    assert headers["x-orcheo-tenant-slug"] == "acme"
    assert headers["x-orcheo-role"] == "admin"
    rebuilt = TenantContext.from_headers({**headers, "quotas": tenant.quotas})
    assert rebuilt.tenant_id == tenant.id
    assert rebuilt.role is Role.ADMIN


def test_tenant_context_from_headers_requires_keys() -> None:
    with pytest.raises(ValueError, match="Missing tenant header"):
        TenantContext.from_headers({})


def test_tenant_context_has_role() -> None:
    tenant = Tenant(slug="acme", name="Acme")
    ctx = TenantContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        user_id="u",
        role=Role.EDITOR,
    )
    assert ctx.has_role(Role.VIEWER)
    assert ctx.has_role(Role.EDITOR)
    assert not ctx.has_role(Role.ADMIN)
