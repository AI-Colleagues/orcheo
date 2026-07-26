"""Dedicated service-identity tests for backend gateway resolution."""

from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orcheo.hosted_apps import (
    AppDeployment,
    AppBinding,
    AppRelease,
    AppSession,
    AppVisibility,
    DeploymentStatus,
    HostedApp,
    InMemoryHostedAppsRepository,
)
from orcheo.models.base import _utcnow
from orcheo_backend.app.hosted_apps import internal as internal_routes
from orcheo_backend.app.hosted_apps.internal import router
from orcheo_backend.app.hosted_apps.runtime_store import reset_app_runtime_service
from orcheo_backend.app.hosted_apps.store import get_hosted_apps_repository


def _client(monkeypatch, *, access_mode: str = "anonymous") -> TestClient:
    repository = InMemoryHostedAppsRepository()
    app = HostedApp(workspace_id=uuid4(), name="Portal", created_by="author")
    repository.create_app_with_alias(app, "gateway-test")
    deployment = AppDeployment(
        workspace_id=app.workspace_id,
        app_id=app.id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository.add_deployment(deployment)
    binding = AppBinding(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="lookup",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode=access_mode,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=1,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={
            "bindings": [
                binding.model_dump(mode="json", exclude={"workspace_id", "app_id"})
            ]
        },
        csp_snapshot={},
        snapshot_sha256="d" * 64,
        created_by="admin",
    )
    repository.publish_release(release)
    repository.set_runtime_enabled(enabled=True, actor="operator")
    monkeypatch.setenv("ORCHEO_APP_GATEWAY_SECRET", "dedicated-secret")
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/apps")
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_hosted_apps_repository] = lambda: repository
    return TestClient(application)


def test_internal_resolution_requires_only_dedicated_gateway_identity(
    monkeypatch,
) -> None:
    client = _client(monkeypatch)
    path = "/internal/hosted-apps/resolve?host=gateway-test.apps.test"
    assert client.get(path).status_code == 401
    assert (
        client.get(
            path,
            headers={
                "X-Orcheo-App-Gateway-Token": "dedicated-secret",
                "Authorization": "Bearer ordinary-service-token",
            },
        ).status_code
        == 401
    )
    response = client.get(
        path, headers={"X-Orcheo-App-Gateway-Token": "dedicated-secret"}
    )
    assert response.status_code == 200
    assert response.json()["host"] == "gateway-test.apps.test"
    assert response.json()["visibility"] == "public"


def test_internal_runtime_resolves_binding_from_release_snapshot(
    monkeypatch,
) -> None:
    reset_app_runtime_service()
    scheduled: list[dict] = []
    monkeypatch.setattr(
        internal_routes,
        "_schedule_local_runtime_run",
        lambda **kwargs: scheduled.append(kwargs),
    )
    client = _client(monkeypatch)
    headers = {"X-Orcheo-App-Gateway-Token": "dedicated-secret"}
    accepted = client.post(
        "/internal/hosted-apps/runtime/runs",
        headers=headers,
        json={
            "host": "gateway-test.apps.test",
            "binding": "lookup",
            "payload": {"query": "hello"},
            "idempotency_key": "same-request",
            "client_ip": "198.51.100.10",
            "anonymous_visitor_id": "c" * 64,
        },
    )
    assert accepted.status_code == 200
    handle = accepted.json()["handle"]
    replay = client.post(
        "/internal/hosted-apps/runtime/runs",
        headers=headers,
        json={
            "host": "gateway-test.apps.test",
            "binding": "lookup",
            "payload": {"query": "hello"},
            "idempotency_key": "same-request",
            "client_ip": "198.51.100.10",
            "anonymous_visitor_id": "c" * 64,
        },
    )
    assert replay.json()["handle"] == handle
    assert len(scheduled) == 1
    assert scheduled[0]["handle"] == handle
    status_response = client.get(
        f"/internal/hosted-apps/runtime/runs/{handle}",
        params={"host": "gateway-test.apps.test"},
        headers=headers,
    )
    assert status_response.json() == {
        "handle": handle,
        "status": "accepted",
        "output": None,
        "error": None,
    }


def test_authenticated_binding_uses_the_exact_host_app_session(monkeypatch) -> None:
    """Gateway session introspection supplies the runtime visitor scope."""
    reset_app_runtime_service()
    monkeypatch.setattr(
        internal_routes, "_schedule_local_runtime_run", lambda **_: None
    )

    async def resolve_session(secret, *, host, descriptor, repository):
        assert secret == "host-cookie-secret"
        assert host == "gateway-test.apps.test"
        now = _utcnow()
        return AppSession(
            app_id=UUID(descriptor["app_id"]),
            workspace_id=UUID(descriptor["workspace_id"]),
            secret_hash="a" * 64,
            app_host=host,
            user_id="member-1",
            runtime_generation=int(descriptor["generation"]),
            expires_at=now + timedelta(hours=1),
            idle_expires_at=now + timedelta(minutes=30),
        )

    monkeypatch.setattr(internal_routes, "_resolve_app_session", resolve_session)
    client = _client(monkeypatch, access_mode="authenticated")
    response = client.post(
        "/internal/hosted-apps/runtime/runs",
        headers={
            "X-Orcheo-App-Gateway-Token": "dedicated-secret",
            "X-Orcheo-App-Session": "host-cookie-secret",
        },
        json={
            "host": "gateway-test.apps.test",
            "binding": "lookup",
            "payload": {"query": "hello"},
            "idempotency_key": "authenticated-request",
            "client_ip": "198.51.100.10",
            "anonymous_visitor_id": "c" * 64,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
