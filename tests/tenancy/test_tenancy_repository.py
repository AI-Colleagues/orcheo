"""Tests for the tenant repositories (in-memory and SQLite)."""

from __future__ import annotations
from collections.abc import Iterator
from pathlib import Path
import pytest
from orcheo.tenancy import (
    InMemoryTenantRepository,
    Role,
    SqliteTenantRepository,
    Tenant,
    TenantAuditEvent,
    TenantMembership,
    TenantMembershipError,
    TenantNotFoundError,
    TenantSlugConflictError,
    TenantStatus,
)


@pytest.fixture(params=["in_memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[object]:
    if request.param == "in_memory":
        yield InMemoryTenantRepository()
    else:
        yield SqliteTenantRepository(tmp_path / "tenants.sqlite")


def _make_tenant(slug: str = "acme", name: str = "Acme Inc") -> Tenant:
    return Tenant(slug=slug, name=name)


def test_create_and_lookup_tenant(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    fetched = repo.get_tenant_by_slug("acme")  # type: ignore[attr-defined]
    assert fetched.id == tenant.id
    by_id = repo.get_tenant(tenant.id)  # type: ignore[attr-defined]
    assert by_id.slug == "acme"


def test_create_tenant_rejects_duplicate_slug(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    repo.create_tenant(_make_tenant())  # type: ignore[attr-defined]
    with pytest.raises(TenantSlugConflictError):
        repo.create_tenant(_make_tenant(name="Acme 2"))  # type: ignore[attr-defined]


def test_get_tenant_by_slug_missing(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    with pytest.raises(TenantNotFoundError):
        repo.get_tenant_by_slug("ghost")  # type: ignore[attr-defined]


def test_list_tenants_filters_inactive(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    a = _make_tenant("aa", "A")
    b = _make_tenant("bb", "B")
    repo.create_tenant(a)  # type: ignore[attr-defined]
    repo.create_tenant(b)  # type: ignore[attr-defined]
    repo.update_status(b.id, TenantStatus.SUSPENDED)  # type: ignore[attr-defined]
    actives = repo.list_tenants()  # type: ignore[attr-defined]
    assert [t.slug for t in actives] == ["aa"]
    full = repo.list_tenants(include_inactive=True)  # type: ignore[attr-defined]
    assert {t.slug for t in full} == {"aa", "bb"}


def test_membership_lifecycle(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.OWNER)
    )
    repo.add_membership(  # type: ignore[attr-defined]
        TenantMembership(tenant_id=tenant.id, user_id="bob", role=Role.EDITOR)
    )
    assert {m.user_id for m in repo.list_memberships_for_tenant(tenant.id)} == {  # type: ignore[attr-defined]
        "alice",
        "bob",
    }
    updated = repo.update_membership_role(tenant.id, "bob", Role.ADMIN)  # type: ignore[attr-defined]
    assert updated.role is Role.ADMIN
    repo.remove_membership(tenant.id, "bob")  # type: ignore[attr-defined]
    with pytest.raises(TenantMembershipError):
        repo.remove_membership(tenant.id, "bob")  # type: ignore[attr-defined]


def test_delete_tenant_cascades_memberships(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.OWNER)
    )
    repo.delete_tenant(tenant.id)  # type: ignore[attr-defined]
    with pytest.raises(TenantNotFoundError):
        repo.get_tenant(tenant.id)  # type: ignore[attr-defined]
    assert repo.list_memberships_for_user("alice") == []  # type: ignore[attr-defined]


def test_soft_delete_sets_deleted_at(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    updated = repo.update_status(tenant.id, TenantStatus.DELETED)  # type: ignore[attr-defined]
    assert updated.deleted_at is not None
    assert updated.status is TenantStatus.DELETED


def test_audit_events_round_trip(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    event = TenantAuditEvent(
        tenant_id=tenant.id,
        action="tenant.suspended",
        actor="admin",
        subject="alice",
        resource_type="tenant",
        resource_id=str(tenant.id),
        details={"reason": "maintenance"},
    )
    stored = repo.record_audit_event(event)  # type: ignore[attr-defined]
    assert stored.action == "tenant.suspended"
    events = repo.list_audit_events(tenant.id)  # type: ignore[attr-defined]
    assert events[-1].action == "tenant.suspended"


def test_add_membership_requires_existing_tenant(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    membership = TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.OWNER)
    with pytest.raises(TenantNotFoundError):
        repo.add_membership(membership)  # type: ignore[attr-defined]


def test_duplicate_membership_blocked(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    tenant = _make_tenant()
    repo.create_tenant(tenant)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.OWNER)
    )
    with pytest.raises(TenantMembershipError):
        repo.add_membership(  # type: ignore[attr-defined]
            TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.EDITOR)
        )
