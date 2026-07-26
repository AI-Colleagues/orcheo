"""Hosted Apps control-plane integration and role-boundary tests."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
from uuid import UUID, uuid4
import zipfile

import pytest
from fastapi.testclient import TestClient

from orcheo.hosted_apps import (
    InMemoryHostedAppsRepository,
)
from orcheo.models import WorkflowDraftAccess
from orcheo.workspace import Role, WorkspaceContext
from orcheo_backend.app.hosted_apps import (
    reset_hosted_apps_repository,
    set_hosted_apps_repository,
)
from orcheo_backend.app.hosted_apps.store import _auto_enable_self_hosted_runtime
from orcheo_backend.app.authentication import RequestContext, get_request_context
from orcheo_backend.app.authentication.dependencies import authenticate_request
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context


@pytest.fixture(autouse=True)
def hosted_apps_environment(monkeypatch: pytest.MonkeyPatch):
    """Enable the fail-closed feature contract for each isolated test."""
    reset_hosted_apps_repository()
    repository = InMemoryHostedAppsRepository()
    set_hosted_apps_repository(repository)
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/orcheo-test-apps")
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", "local")
    yield repository
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
