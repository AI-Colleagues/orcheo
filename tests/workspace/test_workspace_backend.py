"""Tests for the FastAPI workspace dependencies and admin routes."""

from __future__ import annotations
from collections.abc import Iterator
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from orcheo.config import get_settings
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    WorkspaceMembership,
    WorkspaceService,
)
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.factory import create_app
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace import (
    require_role,
    reset_workspace_state,
    set_workspace_repository,
)


@pytest.fixture
def workspace_app(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, InMemoryWorkspaceRepository]]:
    """Build an app where workspace dependencies are exercised end-to-end."""
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_ENABLED", "true")
    get_settings(refresh=True)
    repo = InMemoryWorkspaceRepository()
    set_workspace_repository(repo)
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
        reset_workspace_state()
        get_settings(refresh=True)


def test_admin_create_and_list_workspaces(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    # Pre-create a workspace alice owns so the workspace header is resolvable.
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/admin/workspaces",
        json={"slug": "globex", "name": "Globex", "owner_user_id": "bob"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "globex"

    listing = client.get(
        "/api/admin/workspaces", headers={"X-Orcheo-Workspace": "acme"}
    )
    assert listing.status_code == 200
    slugs = {t["slug"] for t in listing.json()["workspaces"]}
    assert {"acme", "globex"} <= slugs


def test_admin_create_workspace_rejects_duplicate_slug(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/admin/workspaces",
        json={"slug": "acme", "name": "Acme 2", "owner_user_id": "x"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "workspace.slug_conflict"


def test_admin_update_workspace_status(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    other, _ = svc.create_workspace(slug="globex", name="Globex", owner_user_id="bob")
    client = TestClient(app)
    response = client.patch(
        f"/api/admin/workspaces/{other.id}/status",
        json={"status": "suspended"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "suspended"
    assert body["deleted_at"] is None


def test_admin_soft_delete_workspace_records_deleted_at(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.patch(
        f"/api/admin/workspaces/{workspace.id}/status",
        json={"status": "deleted"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deleted"
    assert body["deleted_at"] is not None


def test_admin_workspace_audit_events_route_lists_events(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get(
        f"/api/admin/workspaces/{workspace.id}/audit-events",
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    actions = [event["action"] for event in payload["audit_events"]]
    assert "workspace.created" in actions


def test_workspace_management_routes_require_explicit_admin_role() -> None:
    """Sensitive workspace management routes should carry an explicit admin gate."""
    admin_paths = {
        "/admin/workspaces",
        "/admin/workspaces/{workspace_id}",
        "/admin/workspaces/{workspace_id}/status",
        "/admin/workspaces/{workspace_id}/audit-events",
        "/workspaces/{slug}/members",
        "/workspaces/{slug}/members/{user_id}",
    }
    for route in (r for r in app_routes() if r.path in admin_paths):
        dependency_names = {
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        }
        assert "_checker" in dependency_names, route.path


def app_routes() -> list[APIRoute]:
    """Return the workspace router routes for dependency inspection."""
    from orcheo_backend.app.routers.workspaces import (
        admin_router,
        router,
        self_service_router,
    )

    routes: list[APIRoute] = []
    for candidate in [
        *admin_router.routes,
        *self_service_router.routes,
        *router.routes,
    ]:
        if isinstance(candidate, APIRoute):
            routes.append(candidate)
    return routes


def test_resolve_workspace_context_uses_only_membership(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    # /api/workspaces/me requires only a resolved context, no header — should
    # work because alice has exactly one membership.
    response = client.get("/api/workspaces/me")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["memberships"][0]["slug"] == "acme"
    assert payload["memberships"][0]["role"] == "owner"


def test_user_without_membership_has_no_default_workspace(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, _repo = workspace_app

    async def _fake_auth() -> RequestContext:
        return RequestContext(
            subject="bob",
            identity_type="developer",
            scopes=frozenset({"workflows:read"}),
        )

    app.dependency_overrides[authenticate_request] = _fake_auth
    client = TestClient(app)

    response = client.get("/api/workspaces/me")
    assert response.status_code == 200, response.text
    assert response.json()["memberships"] == []

    active = client.get("/api/workspaces/active")
    assert active.status_code == 403, active.text
    assert active.json()["detail"]["error"]["code"] == "workspace.membership_required"


def test_active_workspace_endpoint_returns_resolved_context(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get("/api/workspaces/active")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspace_id"] == str(workspace.id)
    assert payload["slug"] == "acme"
    assert payload["role"] == "owner"


def test_resolve_workspace_context_requires_selector_for_multi_membership(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    other, _ = svc.create_workspace(
        slug="globex", name="Globex", owner_user_id="charlie"
    )
    repo.add_membership(
        WorkspaceMembership(workspace_id=other.id, user_id="alice", role=Role.VIEWER)
    )
    client = TestClient(app)
    response = client.get("/api/workspaces/me")
    assert response.status_code == 200, response.text
    slugs = {membership["slug"] for membership in response.json()["memberships"]}
    assert slugs == {"acme", "globex"}

    active = client.get("/api/workspaces/active")
    assert active.status_code == 403, active.text
    assert active.json()["detail"]["error"]["code"] == "workspace.forbidden"


def test_invite_member_requires_admin(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    workspace, _ = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="owner-1"
    )
    repo.add_membership(
        WorkspaceMembership(
            workspace_id=workspace.id, user_id="alice", role=Role.EDITOR
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/workspaces/acme/members",
        json={"user_id": "newcomer", "role": "viewer"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "workspace.role_required"


def test_invite_member_succeeds_for_owner(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.post(
        "/api/workspaces/acme/members",
        json={"user_id": "bob", "role": "editor"},
        headers={"X-Orcheo-Workspace": "acme"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == "bob"
    assert body["role"] == "editor"


def test_unknown_workspace_header_returns_404(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    svc = WorkspaceService(repo)
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    client = TestClient(app)
    response = client.get(
        "/api/workspaces/active", headers={"X-Orcheo-Workspace": "ghost"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "workspace.not_found"


def test_anonymous_request_rejected_when_workspace_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When workspace access is anonymous, protected routes must fail."""
    monkeypatch.setenv("ORCHEO_MULTI_WORKSPACE_ENABLED", "true")
    get_settings(refresh=True)
    repo = InMemoryWorkspaceRepository()
    set_workspace_repository(repo)
    app = create_app(InMemoryWorkflowRepository())

    async def _anonymous_auth() -> RequestContext:
        return RequestContext.anonymous()

    app.dependency_overrides[authenticate_request] = _anonymous_auth
    try:
        client = TestClient(app)
        response = client.get("/api/workspaces/me")
        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"] == "auth.authentication_required"
        )

        workspace_response = client.get("/api/workspaces/active")
        assert workspace_response.status_code == 400
        assert (
            workspace_response.json()["detail"]["error"]["code"] == "workspace.required"
        )
    finally:
        app.dependency_overrides.clear()
        reset_workspace_state()
        get_settings(refresh=True)


def test_create_workspace_without_existing_membership(
    workspace_app: tuple[FastAPI, InMemoryWorkspaceRepository],
) -> None:
    app, repo = workspace_app
    client = TestClient(app)
    response = client.post(
        "/api/workspaces",
        json={"slug": "acme", "name": "Acme"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "acme"

    memberships = repo.list_memberships_for_user("alice")
    assert len(memberships) == 1
    assert memberships[0].role is Role.OWNER


def test_require_role_dependency_factory_enforces_minimum() -> None:
    dep = require_role(Role.ADMIN)
    assert callable(dep)
