"""Tests for the high-level tenant service."""

from __future__ import annotations
import pytest
from orcheo.tenancy import (
    InMemoryTenantRepository,
    Role,
    TenantMembershipError,
    TenantPermissionError,
    TenantService,
    TenantStatus,
    ensure_default_tenant,
)


def _service() -> TenantService:
    return TenantService(InMemoryTenantRepository())


def test_create_tenant_assigns_owner_membership() -> None:
    svc = _service()
    tenant, membership = svc.create_tenant(
        slug="acme", name="Acme", owner_user_id="alice"
    )
    assert tenant.slug == "acme"
    assert membership.role is Role.OWNER
    memberships = svc.resolver.list_memberships("alice")
    assert [m.tenant_id for m in memberships] == [tenant.id]


def test_invite_member_and_role_check() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(
        tenant_id=tenant.id,
        user_id="bob",
        role=Role.VIEWER,
        actor_role=Role.OWNER,
    )
    with pytest.raises(TenantPermissionError):
        svc.invite_member(
            tenant_id=tenant.id,
            user_id="charlie",
            role=Role.VIEWER,
            actor_role=Role.EDITOR,
        )


def test_remove_member_invalidates_cache() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(tenant_id=tenant.id, user_id="bob", role=Role.EDITOR)
    assert svc.resolver.list_memberships("bob")
    svc.remove_member(tenant_id=tenant.id, user_id="bob")
    assert svc.resolver.list_memberships("bob") == []


def test_remove_member_requires_admin_actor() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(tenant_id=tenant.id, user_id="bob", role=Role.EDITOR)
    with pytest.raises(TenantPermissionError):
        svc.remove_member(
            tenant_id=tenant.id,
            user_id="bob",
            actor_role=Role.EDITOR,
        )


def test_update_member_role_requires_admin() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(tenant_id=tenant.id, user_id="bob", role=Role.EDITOR)
    updated = svc.update_member_role(
        tenant_id=tenant.id, user_id="bob", role=Role.ADMIN
    )
    assert updated.role is Role.ADMIN
    with pytest.raises(TenantPermissionError):
        svc.update_member_role(
            tenant_id=tenant.id,
            user_id="bob",
            role=Role.OWNER,
            actor_role=Role.EDITOR,
        )


def test_deactivate_and_hard_delete_tenant() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    svc.deactivate_tenant(tenant.id)
    fetched = svc.repository.get_tenant(tenant.id)
    assert fetched.status is TenantStatus.SUSPENDED
    svc.reactivate_tenant(tenant.id)
    assert svc.repository.get_tenant(tenant.id).status is TenantStatus.ACTIVE
    svc.hard_delete_tenant(tenant.id)
    assert svc.resolver.list_memberships("alice") == []


def test_ensure_default_tenant_idempotent() -> None:
    repo = InMemoryTenantRepository()
    a = ensure_default_tenant(repo)
    b = ensure_default_tenant(repo)
    assert a.id == b.id
    assert a.slug == "default"


def test_remove_nonexistent_member_raises() -> None:
    svc = _service()
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    with pytest.raises(TenantMembershipError):
        svc.remove_member(tenant_id=tenant.id, user_id="ghost")
