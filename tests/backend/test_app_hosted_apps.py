"""Hosted Apps control-plane integration and role-boundary tests."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4
import zipfile

import pytest
from fastapi.testclient import TestClient

from orcheo.hosted_apps import (
    AppDeployment,
    AppRelease,
    AppVisibility,
    DeploymentStatus,
    InMemoryHostedAppsRepository,
)
from orcheo.models import WorkflowDraftAccess
from orcheo.workspace import Role, WorkspaceContext, WorkspaceMembership
from orcheo_backend.app.hosted_apps import (
    reset_app_auth_service,
    reset_hosted_apps_repository,
    set_hosted_apps_repository,
)
from orcheo_backend.app.hosted_apps.store import _auto_enable_self_hosted_runtime
from orcheo_backend.app.authentication import RequestContext, get_request_context
from orcheo_backend.app.authentication.dependencies import authenticate_request
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace.dependencies import (
    get_workspace_repository,
    resolve_workspace_context,
)


@pytest.fixture(autouse=True)
def hosted_apps_environment(monkeypatch: pytest.MonkeyPatch):
    """Enable the fail-closed feature contract for each isolated test."""
    reset_hosted_apps_repository()
    reset_app_auth_service()
    repository = InMemoryHostedAppsRepository()
    set_hosted_apps_repository(repository)
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.delenv("ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST", raising=False)
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/orcheo-test-apps")
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", "local")
    yield repository
    reset_app_auth_service()
    reset_hosted_apps_repository()


@pytest.mark.parametrize("deployment_mode", ["local", "single-node"])
def test_ephemeral_startup_can_auto_enable_runtime(
    monkeypatch: pytest.MonkeyPatch,
    deployment_mode: str,
) -> None:
    """Self-hosted stacks can serve apps without a platform bootstrap call."""
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", deployment_mode)
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_AUTO_ENABLE_RUNTIME", "true")
    repository = InMemoryHostedAppsRepository()
    _auto_enable_self_hosted_runtime(repository)
    state = repository.get_runtime_generation()
    assert state.enabled is True
    assert state.updated_by == "system:stack-startup"


def test_app_lifecycle_alias_conflict_and_workspace_denial(
    client: TestClient,
) -> None:
    created = client.post("/api/apps", json={"name": "Portal", "alias": "test-portal"})
    assert created.status_code == 201
    app = created.json()
    assert app["alias"] == "test-portal"
    conflict = client.post("/api/apps", json={"name": "Other", "alias": "test-portal"})
    assert conflict.status_code == 409
    assert client.get("/api/apps").json()["apps"][0]["id"] == app["id"]
    client.post("/api/apps", json={"name": "Third", "alias": "third-app"})
    first_page = client.get("/api/apps", params={"limit": 1}).json()
    assert len(first_page["apps"]) == 1
    assert first_page["next_cursor"]
    second_page = client.get(
        "/api/apps",
        params={"limit": 1, "cursor": first_page["next_cursor"]},
    ).json()
    assert second_page["apps"][0]["id"] != first_page["apps"][0]["id"]

    other_workspace = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="other",
        user_id="viewer",
        role=Role.VIEWER,
    )
    client.app.dependency_overrides[resolve_workspace_context] = lambda: other_workspace
    assert client.get(f"/api/apps/{app['id']}").status_code == 404
    assert (
        client.post(
            "/api/apps", json={"name": "Denied", "alias": "denied-app"}
        ).status_code
        == 403
    )


def test_central_pkce_authorization_requires_current_app_workspace_member(
    client: TestClient,
    hosted_apps_environment: InMemoryHostedAppsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Studio authorization bridge issues only an exact-host member code."""
    auth = RequestContext(subject="member-1", identity_type="user")
    client.app.dependency_overrides[authenticate_request] = lambda: auth
    created = client.post(
        "/api/apps", json={"name": "Private Portal", "alias": "private-portal"}
    ).json()
    workspace_id = UUID(created["workspace_id"])
    app_id = UUID(created["id"])
    get_workspace_repository().add_membership(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=auth.subject,
            role=Role.VIEWER,
        )
    )
    deployment = AppDeployment(
        workspace_id=workspace_id,
        app_id=app_id,
        status=DeploymentStatus.READY,
        created_by="member-1",
    )
    hosted_apps_environment.add_deployment(deployment)
    hosted_apps_environment.publish_release(
        AppRelease(
            workspace_id=workspace_id,
            app_id=app_id,
            deployment_id=deployment.id,
            permission_revision=created["permission_revision"],
            visibility=AppVisibility.PRIVATE,
            capability_snapshot={"bindings": []},
            csp_snapshot={},
            snapshot_sha256="a" * 64,
            created_by="member-1",
        )
    )
    hosted_apps_environment.set_runtime_enabled(enabled=True, actor="operator")
    state = "s" * 43
    response = client.post(
        "/api/hosted-apps/auth/authorize",
        json={
            "host": "private-portal.apps.test",
            "redirect_uri": ("https://private-portal.apps.test/__orcheo/auth/callback"),
            "code_challenge": "c" * 43,
            "state": state,
        },
    )
    assert response.status_code == 200
    callback = urlparse(response.json()["redirect_url"])
    assert callback.netloc == "private-portal.apps.test"
    assert parse_qs(callback.query)["state"] == [state]
    assert parse_qs(callback.query)["code"]

    assert (
        client.post(
            "/api/hosted-apps/auth/authorize",
            json={
                "host": "private-portal.apps.test",
                "redirect_uri": "https://wrong.apps.test/__orcheo/auth/callback",
                "code_challenge": "c" * 43,
                "state": state,
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/hosted-apps/auth/authorize",
            json={
                "host": "missing.apps.test",
                "redirect_uri": "https://missing.apps.test/__orcheo/auth/callback",
                "code_challenge": "c" * 43,
                "state": state,
            },
        ).status_code
        == 404
    )
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.localhost")
    client.app.dependency_overrides[authenticate_request] = lambda: auth
    assert (
        client.post(
            "/api/hosted-apps/auth/authorize",
            json={
                "host": "private-portal.apps.localhost",
                "redirect_uri": "http://private-portal.apps.localhost/__orcheo/auth/callback",
                "code_challenge": "c" * 43,
                "state": state,
            },
        ).status_code
        == 200
    )
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    client.app.dependency_overrides[authenticate_request] = lambda: RequestContext(
        subject="not-a-member", identity_type="user"
    )
    assert (
        client.post(
            "/api/hosted-apps/auth/authorize",
            json={
                "host": "private-portal.apps.test",
                "redirect_uri": "https://private-portal.apps.test/__orcheo/auth/callback",
                "code_challenge": "c" * 43,
                "state": state,
            },
        ).status_code
        == 403
    )


def test_collection_crud_uses_stable_ids_and_updates_review_revision(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/apps", json={"name": "Portal", "alias": "data-portal"}
    ).json()
    app_id = created["id"]
    collection = {
        "name": "preferences",
        "scope": "user",
        "read_access": "authenticated",
        "write_access": "authenticated",
        "max_document_bytes": 4096,
        "max_records": 100,
    }
    response = client.post(f"/api/apps/{app_id}/collections", json=collection)
    assert response.status_code == 201
    first_id = response.json()["id"]
    assert client.get(f"/api/apps/{app_id}").json()["permission_revision"] == 2
    assert (
        client.delete(f"/api/apps/{app_id}/collections/{first_id}").status_code == 204
    )
    replacement = client.post(f"/api/apps/{app_id}/collections", json=collection)
    assert replacement.status_code == 201
    assert replacement.json()["id"] != first_id
    actions = [
        event["action"] for event in client.get(f"/api/apps/{app_id}/audit").json()
    ]
    assert "capability.collection.delete" in actions


def test_workspace_owner_is_denied_platform_moderation_without_scope(
    client: TestClient,
) -> None:
    owner = RequestContext(
        subject="workspace-owner",
        identity_type="user",
        scopes=frozenset(),
    )
    client.app.dependency_overrides[authenticate_request] = lambda: owner
    client.app.dependency_overrides[get_request_context] = lambda: owner
    denied = client.post(
        "/api/platform/hosted-apps/blocks",
        json={
            "target_kind": "alias",
            "target_id": "blocked-app",
            "reason_code": "abuse",
        },
    )
    assert denied.status_code == 403
    operator = RequestContext(
        subject="operator",
        identity_type="service",
        scopes=frozenset({"platform:hosted-apps:moderate"}),
    )
    client.app.dependency_overrides[authenticate_request] = lambda: operator
    client.app.dependency_overrides[get_request_context] = lambda: operator
    created = client.post(
        "/api/platform/hosted-apps/blocks",
        json={
            "target_kind": "alias",
            "target_id": "blocked-app",
            "reason_code": "abuse",
            "reason_detail": "verified report",
        },
    )
    assert created.status_code == 201
    reinstated = client.post(
        f"/api/platform/hosted-apps/blocks/{created.json()['id']}/reinstate"
    )
    assert reinstated.status_code == 200
    assert reinstated.json()["lifted_by"] == "operator"


def test_local_bundle_upload_validates_and_publishes(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local editor can upload a safe ZIP and publish its immutable deployment."""
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(tmp_path))
    created = client.post(
        "/api/apps", json={"name": "Example App", "alias": "example-upload"}
    ).json()
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "index.html",
            "<!doctype html><title>Example</title><h1>Hosted Apps works</h1>",
        )
        bundle.writestr("assets/app.css", "body { font-family: sans-serif; }")
    payload = archive.getvalue()

    uploaded = client.post(
        f"/api/apps/{created['id']}/deployments/upload",
        files={"bundle": ("example.zip", payload, "application/zip")},
    )
    assert uploaded.status_code == 201
    deployment = uploaded.json()
    assert deployment["status"] == "ready"
    assert deployment["archive_sha256"]
    assert (tmp_path / "deployments" / deployment["id"] / "__manifest__.json").is_file()

    published = client.post(
        f"/api/apps/{created['id']}/deployments/{deployment['id']}/publish",
        json={"acknowledged_permission_revision": created["permission_revision"]},
    )
    assert published.status_code == 200
    assert published.json()["state"] == "published"
    assert (
        client.get(f"/api/apps/{created['id']}").json()["active_deployment_id"]
        == deployment["id"]
    )


def test_manifest_upload_resolves_two_workflows_and_freezes_release(
    client: TestClient,
    repository: InMemoryWorkflowRepository,
    hosted_apps_environment: InMemoryHostedAppsRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin publishes exact grants resolved from a portable bundle manifest."""
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(tmp_path))
    created = client.post(
        "/api/apps", json={"name": "Two workflows", "alias": "two-workflows"}
    ).json()
    workspace_id = created["workspace_id"]

    async def seed_workflows() -> None:
        for name, handle in (
            ("Greeting", "hosted-app-greeting"),
            ("Farewell", "hosted-app-farewell"),
        ):
            workflow = await repository.create_workflow(
                name=name,
                handle=handle,
                slug=None,
                description=None,
                tags=None,
                draft_access=WorkflowDraftAccess.PERSONAL,
                actor="test-user",
                workspace_id=workspace_id,
            )
            await repository.create_version(
                workflow.id,
                graph={},
                metadata={},
                notes=None,
                created_by="test-user",
            )

    asyncio.run(seed_workflows())
    binding_policy = {
        "access_mode": "anonymous",
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "maxLength": 80}},
            "additionalProperties": False,
        },
        "output_projection": {"fields": ["final_state"]},
        "visitor_can_read_output": True,
        "visitor_can_read_sanitized_errors": True,
        "limits": {"timeout_seconds": 60},
    }
    app_manifest = {
        "schema_version": 1,
        "bindings": {
            "greet": {
                "workflow": "hosted-app-greeting",
                "version": 1,
                **binding_policy,
            },
            "farewell": {
                "workflow": "hosted-app-farewell",
                "version": 1,
                **binding_policy,
            },
        },
    }
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("index.html", "<!doctype html><h1>Two workflows</h1>")
        bundle.writestr("orcheo.app.json", json.dumps(app_manifest))

    uploaded = client.post(
        f"/api/apps/{created['id']}/deployments/upload",
        files={
            "bundle": (
                "two-workflows.zip",
                archive.getvalue(),
                "application/zip",
            )
        },
    )

    assert uploaded.status_code == 201
    deployment = uploaded.json()
    assert set(deployment["app_manifest"]["bindings"]) == {"greet", "farewell"}
    assert not (
        tmp_path / "deployments" / deployment["id"] / "orcheo.app.json"
    ).exists()
    reviewed_revision = client.get(f"/api/apps/{created['id']}").json()[
        "permission_revision"
    ]
    assert reviewed_revision == created["permission_revision"] + 1

    published = client.post(
        f"/api/apps/{created['id']}/deployments/{deployment['id']}/publish",
        json={"acknowledged_permission_revision": reviewed_revision},
    )

    assert published.status_code == 200
    release_id = UUID(published.json()["active_release_id"])
    release = hosted_apps_environment._releases[release_id]  # noqa: SLF001
    bindings = release.capability_snapshot["bindings"]
    assert [binding["name"] for binding in bindings] == ["farewell", "greet"]
    assert len({binding["workflow_id"] for binding in bindings}) == 2
    assert all(binding["workflow_execution_sha256"] for binding in bindings)
    assert client.get(f"/api/apps/{created['id']}/bindings").json() == []


def test_draft_mutations_bindings_and_collections_cover_control_plane_branches(
    client: TestClient,
    repository: InMemoryWorkflowRepository,
) -> None:
    """Draft policy, capability, and collection endpoints preserve stable identities."""
    created = client.post(
        "/api/apps", json={"name": "Portal", "alias": "control-plane"}
    ).json()
    app_id = created["id"]
    updated = client.patch(
        f"/api/apps/{app_id}",
        json={"name": "Renamed", "description": "draft"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    archived = client.post(f"/api/apps/{app_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    restored = client.post(f"/api/apps/{app_id}/restore")
    assert restored.status_code == 200
    replacement = client.put(
        f"/api/apps/{app_id}/alias", json={"alias": "control-plane-new"}
    )
    assert replacement.status_code == 200

    async def seed() -> tuple[UUID, UUID]:
        workflow = await repository.create_workflow(
            name="Lookup",
            handle="control-plane-workflow",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="test-user",
            workspace_id=created["workspace_id"],
        )
        version = await repository.create_version(
            workflow.id,
            graph={"nodes": []},
            metadata={},
            notes=None,
            created_by="test-user",
        )
        return workflow.id, version.id

    workflow_id, version_id = asyncio.run(seed())
    binding = {
        "name": "lookup",
        "workflow_id": str(workflow_id),
        "workflow_version_id": str(version_id),
        "access_mode": "anonymous",
        "input_schema": {"type": "object"},
        "output_projection": {"fields": ["answer"]},
        "limits": {"timeout_seconds": 60},
    }
    created_binding = client.post(f"/api/apps/{app_id}/bindings", json=binding)
    assert created_binding.status_code == 201
    binding_id = created_binding.json()["id"]
    changed_binding = dict(binding, name="lookup-v2")
    changed = client.put(
        f"/api/apps/{app_id}/bindings/{binding_id}", json=changed_binding
    )
    assert changed.status_code == 200
    assert changed.json()["id"] == binding_id
    invalid_update = client.put(
        f"/api/apps/{app_id}/bindings/{binding_id}",
        json=dict(changed_binding, input_schema={"type": "object", "pattern": "bad"}),
    )
    assert invalid_update.status_code == 400
    assert client.delete(f"/api/apps/{app_id}/bindings/{binding_id}").status_code == 204
    bad_binding = client.post(
        f"/api/apps/{app_id}/bindings",
        json=dict(binding, input_schema={"type": "object", "pattern": "bad"}),
    )
    assert bad_binding.status_code == 400

    collection = {
        "name": "records",
        "scope": "shared",
        "read_access": "anonymous",
        "write_access": "anonymous",
        "max_document_bytes": 4096,
        "max_records": 10,
    }
    created_collection = client.post(f"/api/apps/{app_id}/collections", json=collection)
    assert created_collection.status_code == 201
    collection_id = created_collection.json()["id"]
    changed_collection = client.put(
        f"/api/apps/{app_id}/collections/{collection_id}",
        json=dict(collection, max_records=20),
    )
    assert changed_collection.status_code == 200
    assert (
        client.delete(f"/api/apps/{app_id}/collections/{collection_id}").status_code
        == 204
    )
    assert client.get(f"/api/apps/{app_id}/deployments").status_code == 200
    assert client.get(f"/api/apps/{app_id}/audit").status_code == 200
    assert client.get("/api/apps", params={"cursor": "__8"}).status_code == 400
    assert client.get(f"/api/apps/{uuid4()}").status_code == 404


def test_hosted_apps_upload_error_contract_and_platform_operations(
    client: TestClient,
    hosted_apps_environment: InMemoryHostedAppsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload validation and platform operations expose stable error/status codes."""
    created = client.post(
        "/api/apps", json={"name": "Upload", "alias": "upload-errors"}
    ).json()
    app_id = created["id"]
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/upload",
            files={"bundle": ("not-a-zip.txt", b"bytes", "text/plain")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/upload",
            files={"bundle": ("empty.zip", b"", "application/zip")},
        ).status_code
        == 413
    )
    operator = RequestContext(
        subject="operator",
        identity_type="service",
        scopes=frozenset(
            {"platform:hosted-apps:moderate", "platform:hosted-apps:runtime-control"}
        ),
    )
    client.app.dependency_overrides[authenticate_request] = lambda: operator
    client.app.dependency_overrides[get_request_context] = lambda: operator
    reserved = client.post(
        "/api/platform/hosted-apps/reserved-aliases", json={"alias": "reserved"}
    )
    assert reserved.status_code == 201
    assert (
        client.post(
            "/api/platform/hosted-apps/reserved-aliases", json={"alias": "reserved"}
        ).status_code
        == 409
    )
    assert (
        client.get("/api/platform/hosted-apps/aliases/unknown/owner").status_code == 404
    )
    assert (
        client.get("/api/platform/hosted-apps/aliases/not%40valid/owner").status_code
        == 404
    )
    assert (
        client.get("/api/platform/hosted-apps/aliases/reserved/owner").json()["kind"]
        == "platform"
    )
    assert client.get("/api/platform/hosted-apps/runtime").status_code == 200
    changed = client.put("/api/platform/hosted-apps/runtime", json={"enabled": True})
    assert changed.status_code == 200
    assert client.get("/api/platform/hosted-apps/runtime").json()["enabled"] is True
    missing_reinstate = client.post(
        f"/api/platform/hosted-apps/blocks/{uuid4()}/reinstate"
    )
    assert missing_reinstate.status_code == 404
    monkeypatch.setattr(
        hosted_apps_environment,
        "get_runtime_generation",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert client.get("/api/platform/hosted-apps/runtime").status_code == 503


def test_hosted_apps_route_error_contracts_and_configuration_guards(
    client: TestClient,
    hosted_apps_environment: InMemoryHostedAppsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace APIs return stable errors for missing resources and bad drafts."""
    created = client.post(
        "/api/apps", json={"name": "Route Errors", "alias": "route-errors"}
    ).json()
    app_id = created["id"]
    other = client.post(
        "/api/apps", json={"name": "Other Route", "alias": "other-route"}
    ).json()
    assert (
        client.post("/api/apps", json={"name": "Bad Alias", "alias": "api"}).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/apps/{app_id}", json={"name": "Renamed", "description": "desc"}
        ).status_code
        == 200
    )
    assert (
        client.patch(f"/api/apps/{app_id}", json={"visibility": "private"}).status_code
        == 200
    )
    missing = str(uuid4())
    for path, method in (
        (f"/api/apps/{missing}", "get"),
        (f"/api/apps/{missing}", "patch"),
        (f"/api/apps/{missing}/archive", "post"),
        (f"/api/apps/{missing}/restore", "post"),
        (f"/api/apps/{missing}/alias", "put"),
        (f"/api/apps/{missing}/bindings", "get"),
        (f"/api/apps/{missing}/collections", "get"),
        (f"/api/apps/{missing}/audit", "get"),
        (f"/api/apps/{missing}/deployments", "get"),
        (f"/api/apps/{missing}/deployments/upload", "post"),
        (f"/api/apps/{missing}/unpublish", "post"),
    ):
        kwargs = {}
        if method == "patch":
            kwargs["json"] = {"name": "missing"}
        elif method == "put":
            kwargs["json"] = {"alias": "new-route"}
        elif method == "post" and path.endswith("upload"):
            kwargs["files"] = {"bundle": ("empty.zip", b"", "application/zip")}
        assert getattr(client, method)(path, **kwargs).status_code == 404

    assert (
        client.put(
            f"/api/apps/{app_id}/alias", json={"alias": other["alias"]}
        ).status_code
        == 409
    )
    assert (
        client.put(f"/api/apps/{app_id}/alias", json={"alias": "api"}).status_code
        == 400
    )
    binding = {
        "name": "missing-binding",
        "workflow_id": str(uuid4()),
        "workflow_version_id": str(uuid4()),
        "access_mode": "anonymous",
    }
    assert client.post(f"/api/apps/{missing}/bindings", json=binding).status_code == 404
    assert (
        client.put(f"/api/apps/{app_id}/bindings/{uuid4()}", json=binding).status_code
        == 404
    )
    assert client.delete(f"/api/apps/{app_id}/bindings/{uuid4()}").status_code == 404
    assert (
        client.post(
            f"/api/apps/{missing}/collections",
            json={
                "name": "missing-data",
                "scope": "shared",
                "read_access": "anonymous",
                "write_access": "anonymous",
                "max_document_bytes": 100,
                "max_records": 10,
            },
        ).status_code
        == 404
    )
    collection = {
        "name": "route-data",
        "scope": "shared",
        "read_access": "anonymous",
        "write_access": "anonymous",
        "max_document_bytes": 100,
        "max_records": 10,
    }
    first = client.post(f"/api/apps/{app_id}/collections", json=collection)
    assert first.status_code == 201
    assert client.get(f"/api/apps/{app_id}/collections").status_code == 200
    assert (
        client.post(f"/api/apps/{app_id}/collections", json=collection).status_code
        == 409
    )
    collection_id = first.json()["id"]
    assert (
        client.put(
            f"/api/apps/{app_id}/collections/{uuid4()}", json=collection
        ).status_code
        == 404
    )
    assert client.delete(f"/api/apps/{app_id}/collections/{uuid4()}").status_code == 404
    second_collection = dict(collection, name="route-data-2")
    second = client.post(f"/api/apps/{app_id}/collections", json=second_collection)
    assert second.status_code == 201
    assert (
        client.put(
            f"/api/apps/{app_id}/collections/{second.json()['id']}", json=collection
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/{uuid4()}/publish",
            json={"acknowledged_permission_revision": 1},
        ).status_code
        == 404
    )
    editor = WorkspaceContext(
        workspace_id=UUID(created["workspace_id"]),
        workspace_slug="test",
        user_id="editor",
        role=Role.EDITOR,
    )
    original_workspace_override = client.app.dependency_overrides.get(
        resolve_workspace_context
    )
    client.app.dependency_overrides[resolve_workspace_context] = lambda: editor
    assert (
        client.patch(f"/api/apps/{app_id}", json={"visibility": "private"}).status_code
        == 403
    )
    if original_workspace_override is not None:
        client.app.dependency_overrides[resolve_workspace_context] = (
            original_workspace_override
        )
    else:
        client.app.dependency_overrides.pop(resolve_workspace_context, None)
    assert client.post(f"/api/apps/{app_id}/unpublish").status_code == 200
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "s3")
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/upload",
            files={"bundle": ("bundle.zip", b"zip", "application/zip")},
        ).status_code
        == 409
    )
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/orcheo-route-errors")
    invalid_archive = BytesIO()
    with zipfile.ZipFile(invalid_archive, "w") as bundle:
        bundle.writestr("missing-index.js", "x")
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/upload",
            files={
                "bundle": ("invalid.zip", invalid_archive.getvalue(), "application/zip")
            },
        ).status_code
        == 422
    )
    manifest_archive = BytesIO()
    with zipfile.ZipFile(manifest_archive, "w") as bundle:
        bundle.writestr("index.html", "<h1>manifest</h1>")
        bundle.writestr(
            "orcheo.app.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "bindings": {
                        "missing": {
                            "workflow": "missing-workflow",
                            "version": 1,
                            "access_mode": "anonymous",
                        }
                    },
                }
            ),
        )
    manifest_upload = client.post(
        f"/api/apps/{app_id}/deployments/upload",
        files={
            "bundle": ("manifest.zip", manifest_archive.getvalue(), "application/zip")
        },
    )
    assert manifest_upload.status_code == 422
    failed_deployment = hosted_apps_environment.list_deployments(
        UUID(created["workspace_id"]), UUID(app_id)
    )[-1]
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/{failed_deployment.id}/publish",
            json={"acknowledged_permission_revision": created["permission_revision"]},
        ).status_code
        == 409
    )
    valid_archive = BytesIO()
    with zipfile.ZipFile(valid_archive, "w") as bundle:
        bundle.writestr("index.html", "<h1>valid</h1>")
    valid_upload = client.post(
        f"/api/apps/{app_id}/deployments/upload",
        files={"bundle": ("valid.zip", valid_archive.getvalue(), "application/zip")},
    )
    assert valid_upload.status_code == 201
    valid_deployment = hosted_apps_environment.list_deployments(
        UUID(created["workspace_id"]), UUID(app_id)
    )[-1]
    assert (
        client.post(
            f"/api/apps/{app_id}/deployments/{valid_deployment.id}/publish",
            json={"acknowledged_permission_revision": created["permission_revision"]},
        ).status_code
        == 409
    )
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/orcheo-test-apps")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST", str(uuid4()))
    assert client.get("/api/apps").status_code == 404
    monkeypatch.setenv(
        "ORCHEO_HOSTED_APPS_WORKSPACE_ALLOWLIST", str(created["workspace_id"])
    )
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "not-a-bool")
    assert client.get("/api/apps").status_code == 503
