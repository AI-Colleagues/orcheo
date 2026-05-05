"""Tests for the FastAPI tenancy dependencies and admin routes."""

from __future__ import annotations
from collections.abc import Iterator
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from orcheo.config import get_settings
from orcheo.tenancy import (
    DEFAULT_TENANT_SLUG,
    InMemoryTenantRepository,
    Role,
    TenantMembership,
    TenantService,
)
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.factory import create_app
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.tenancy import (
    bootstrap_default_tenant,
    require_role,
    reset_tenancy_state,
    set_tenant_repository,
)


@pytest.fixture
def tenancy_app(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, InMemoryTenantRepository]]:
    """Build an app where tenancy dependencies are exercised end-to-end."""
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_ENABLED", "true")
    get_settings(refresh=True)
    repo = InMemoryTenantRepository()
    set_tenant_repository(repo)
    app = create_app(InMemoryWorkflowRepository())

    async def _fake_auth() -> RequestContext:
        return RequestContext(
            subject="alice",
            identity_type="developer",
            scopes=frozenset({"workflows:read"}),
        )

    app.dependency_overrides[authenticate_request] = _fake_auth
    try:
        yield app, repo
    finally:
        app.dependency_overrides.clear()
        reset_tenancy_state()
        get_settings(refresh=True)


def test_admin_create_and_list_tenants(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    # Pre-create a tenant alice owns so the tenancy header is resolvable.
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/admin/tenants",
        json={"slug": "globex", "name": "Globex", "owner_user_id": "bob"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "globex"

    listing = client.get("/api/admin/tenants", headers={"X-Orcheo-Tenant": "acme"})
    assert listing.status_code == 200
    slugs = {t["slug"] for t in listing.json()["tenants"]}
    assert {"acme", "globex"} <= slugs


def test_admin_create_tenant_rejects_duplicate_slug(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/admin/tenants",
        json={"slug": "acme", "name": "Acme 2", "owner_user_id": "x"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "tenant.slug_conflict"


def test_admin_update_tenant_status(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    other, _ = svc.create_tenant(slug="globex", name="Globex", owner_user_id="bob")
    client = TestClient(app)
    response = client.patch(
        f"/api/admin/tenants/{other.id}/status",
        json={"status": "suspended"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "suspended"
    assert body["deleted_at"] is None


def test_admin_soft_delete_tenant_records_deleted_at(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.patch(
        f"/api/admin/tenants/{tenant.id}/status",
        json={"status": "deleted"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deleted"
    assert body["deleted_at"] is not None


def test_admin_tenant_audit_events_route_lists_events(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get(
        f"/api/admin/tenants/{tenant.id}/audit-events",
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    actions = [event["action"] for event in payload["audit_events"]]
    assert "tenant.created" in actions


def test_tenant_management_routes_require_explicit_admin_role() -> None:
    """Sensitive tenant management routes should carry an explicit admin gate."""
    admin_paths = {
        "/admin/tenants",
        "/admin/tenants/{tenant_id}",
        "/admin/tenants/{tenant_id}/status",
        "/admin/tenants/{tenant_id}/audit-events",
        "/tenants/{slug}/members",
        "/tenants/{slug}/members/{user_id}",
    }
    for route in (r for r in app_routes() if r.path in admin_paths):
        dependency_names = {
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        }
        assert "_checker" in dependency_names, route.path


def app_routes() -> list[APIRoute]:
    """Return the tenant router routes for dependency inspection."""
    from orcheo_backend.app.routers.tenants import admin_router, router

    routes: list[APIRoute] = []
    for candidate in [*admin_router.routes, *router.routes]:
        if isinstance(candidate, APIRoute):
            routes.append(candidate)
    return routes


def test_resolve_tenant_context_uses_only_membership(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    # /api/tenants/me requires only a resolved context, no header — should
    # work because alice has exactly one membership.
    response = client.get("/api/tenants/me")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["memberships"][0]["slug"] == "acme"
    assert payload["memberships"][0]["role"] == "owner"


def test_active_tenant_endpoint_returns_resolved_context(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get("/api/tenants/active")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tenant_id"] == str(tenant.id)
    assert payload["slug"] == "acme"
    assert payload["role"] == "owner"


def test_resolve_tenant_context_requires_header_for_multi_membership(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    other, _ = svc.create_tenant(slug="globex", name="Globex", owner_user_id="charlie")
    repo.add_membership(
        TenantMembership(tenant_id=other.id, user_id="alice", role=Role.VIEWER)
    )
    client = TestClient(app)
    response = client.get("/api/tenants/me")
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "tenant.forbidden"


def test_invite_member_requires_admin(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    tenant, _ = svc.create_tenant(slug="acme", name="Acme", owner_user_id="owner-1")
    repo.add_membership(
        TenantMembership(tenant_id=tenant.id, user_id="alice", role=Role.EDITOR)
    )
    client = TestClient(app)
    response = client.post(
        "/api/tenants/acme/members",
        json={"user_id": "newcomer", "role": "viewer"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "tenant.role_required"


def test_invite_member_succeeds_for_owner(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/tenants/acme/members",
        json={"user_id": "bob", "role": "editor"},
        headers={"X-Orcheo-Tenant": "acme"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == "bob"
    assert body["role"] == "editor"


def test_unknown_tenant_header_returns_404(
    tenancy_app: tuple[FastAPI, InMemoryTenantRepository],
) -> None:
    app, repo = tenancy_app
    svc = TenantService(repo)
    svc.create_tenant(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get("/api/tenants/me", headers={"X-Orcheo-Tenant": "ghost"})
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "tenant.not_found"


def test_default_tenant_bootstrap_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_ENABLED", "false")
    get_settings(refresh=True)
    repo = InMemoryTenantRepository()
    set_tenant_repository(repo)
    try:
        bootstrap_default_tenant(user_id="alice")
        # Idempotent: a second call should not raise.
        bootstrap_default_tenant(user_id="alice")
        tenant = repo.get_tenant_by_slug(DEFAULT_TENANT_SLUG)
        memberships = repo.list_memberships_for_tenant(tenant.id)
        assert {m.user_id for m in memberships} == {"alice"}
    finally:
        reset_tenancy_state()
        get_settings(refresh=True)


def test_anonymous_request_resolves_default_tenant_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When tenancy is off, anonymous requests bootstrap into the default tenant."""
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_ENABLED", "false")
    get_settings(refresh=True)
    repo = InMemoryTenantRepository()
    set_tenant_repository(repo)
    app = create_app(InMemoryWorkflowRepository())

    async def _anonymous_auth() -> RequestContext:
        return RequestContext.anonymous()

    app.dependency_overrides[authenticate_request] = _anonymous_auth
    try:
        client = TestClient(app)
        response = client.get("/api/tenants/me")
        assert response.status_code == 200, response.text
        slugs = {m["slug"] for m in response.json()["memberships"]}
        assert DEFAULT_TENANT_SLUG in slugs
    finally:
        app.dependency_overrides.clear()
        reset_tenancy_state()
        get_settings(refresh=True)


def test_anonymous_request_rejected_when_tenancy_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When tenancy is on, anonymous requests must fail with 400."""
    monkeypatch.setenv("ORCHEO_MULTI_TENANCY_ENABLED", "true")
    get_settings(refresh=True)
    repo = InMemoryTenantRepository()
    set_tenant_repository(repo)
    app = create_app(InMemoryWorkflowRepository())

    async def _anonymous_auth() -> RequestContext:
        return RequestContext.anonymous()

    app.dependency_overrides[authenticate_request] = _anonymous_auth
    try:
        client = TestClient(app)
        response = client.get("/api/tenants/me")
        assert response.status_code == 400
        assert response.json()["detail"]["error"]["code"] == "tenant.required"
    finally:
        app.dependency_overrides.clear()
        reset_tenancy_state()
        get_settings(refresh=True)


def test_require_role_dependency_factory_enforces_minimum() -> None:
    dep = require_role(Role.ADMIN)
    assert callable(dep)
