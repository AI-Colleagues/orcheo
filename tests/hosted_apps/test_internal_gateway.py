"""Dedicated service-identity tests for backend gateway resolution."""

from datetime import timedelta
import asyncio
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
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
    FilesystemBundleStore,
    HostedApp,
    InMemoryHostedAppsRepository,
    AppAuthError,
    AppRuntimeConflictError,
    AppRuntimeError,
    AppRuntimeLimitError,
)
from orcheo.models.base import _utcnow
from orcheo.workspace import WorkspaceMembershipError
from orcheo_backend.app.hosted_apps import internal as internal_routes
from orcheo_backend.app.hosted_apps.internal import router
from orcheo_backend.app.hosted_apps.runtime_store import reset_app_runtime_service
from orcheo_backend.app.hosted_apps.store import get_hosted_apps_repository
from orcheo_backend.app.hosted_apps.store import (
    reset_app_bundle_store,
    set_app_bundle_store,
)


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


def test_internal_gateway_reads_private_bundle_asset(tmp_path, monkeypatch) -> None:
    deployment_id = uuid4()
    store = FilesystemBundleStore(tmp_path)
    store.write_deployment_file(
        deployment_id,
        "index.html",
        BytesIO(b"<h1>App</h1>"),
    )
    set_app_bundle_store(store)
    try:
        client = _client(monkeypatch)
        path = f"/internal/hosted-apps/deployments/{deployment_id}/assets/index.html"
        assert client.get(path).status_code == 401
        response = client.get(
            path,
            headers={"X-Orcheo-App-Gateway-Token": "dedicated-secret"},
        )
        assert response.status_code == 200
        assert response.content == b"<h1>App</h1>"
        assert response.headers["cache-control"] == "private, no-store"
    finally:
        reset_app_bundle_store()


def test_internal_gateway_deployment_asset_error_contracts(monkeypatch) -> None:
    class _RaisingStore:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def open_deployment_file(self, deployment_id: UUID, asset_path: str):
            raise self._exc

    deployment_id = uuid4()
    client = _client(monkeypatch)
    path = f"/internal/hosted-apps/deployments/{deployment_id}/assets/index.html"
    headers = {"X-Orcheo-App-Gateway-Token": "dedicated-secret"}

    set_app_bundle_store(_RaisingStore(FileNotFoundError()))
    try:
        response = client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.json()["detail"] == "Deployment asset was not found."
    finally:
        reset_app_bundle_store()

    set_app_bundle_store(_RaisingStore(ValueError("bad path")))
    try:
        response = client.get(path, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"] == "Deployment asset path is invalid."
    finally:
        reset_app_bundle_store()


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


def test_internal_gateway_error_contracts(monkeypatch) -> None:
    """Gateway routes map authorization, governance, and lookup failures safely."""
    reset_app_runtime_service()
    client = _client(monkeypatch)
    headers = {"X-Orcheo-App-Gateway-Token": "dedicated-secret"}
    body = {
        "host": "gateway-test.apps.test",
        "binding": "lookup",
        "payload": {"query": "hello"},
        "idempotency_key": "error-request",
        "client_ip": "198.51.100.10",
        "anonymous_visitor_id": "c" * 64,
    }
    assert (
        client.get(
            "/internal/hosted-apps/resolve?host=invalid.apps.test",
            headers=headers,
        ).status_code
        == 404
    )
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "false")
    assert (
        client.get(
            "/internal/hosted-apps/resolve?host=gateway-test.apps.test",
            headers=headers,
        ).status_code
        == 503
    )
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    with monkeypatch.context() as local:
        local.setattr(
            internal_routes.HostedAppsSettings,
            "from_environment",
            classmethod(lambda cls: SimpleNamespace(enabled=True, base_domain=None)),
        )
        assert (
            client.get(
                "/internal/hosted-apps/resolve?host=gateway-test.apps.test",
                headers=headers,
            ).status_code
            == 503
        )
    assert (
        client.post(
            "/internal/hosted-apps/runtime/runs",
            headers=headers,
            json={**body, "binding": "missing"},
        ).status_code
        == 404
    )

    class RaisingRuntime:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def accept(self, *_args, **_kwargs):
            raise self.error

        def status(self, *_args, **_kwargs):
            raise self.error

    for error, expected in (
        (AppRuntimeLimitError("limited"), 429),
        (AppRuntimeConflictError("conflict"), 409),
        (AppRuntimeError("missing"), 404),
    ):
        monkeypatch.setattr(
            internal_routes,
            "get_app_runtime_service",
            lambda _repo, error=error: RaisingRuntime(error),
        )
        response = client.post(
            "/internal/hosted-apps/runtime/runs", headers=headers, json=body
        )
        assert response.status_code == expected
    monkeypatch.setattr(
        internal_routes,
        "get_app_runtime_service",
        lambda _repo: RaisingRuntime(AppAuthError("unauthorized")),
    )
    assert (
        client.get(
            "/internal/hosted-apps/runtime/runs/opaque",
            params={"host": "gateway-test.apps.test"},
            headers=headers,
        ).status_code
        == 401
    )
    monkeypatch.setattr(
        internal_routes,
        "get_app_runtime_service",
        lambda _repo: RaisingRuntime(AppRuntimeError("missing")),
    )
    assert (
        client.get(
            "/internal/hosted-apps/runtime/runs/opaque",
            params={"host": "gateway-test.apps.test"},
            headers=headers,
        ).status_code
        == 404
    )
    monkeypatch.setattr(
        internal_routes,
        "_resolve_app_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppAuthError("bad session")),
    )
    assert (
        client.post(
            "/internal/hosted-apps/runtime/runs", headers=headers, json=body
        ).status_code
        == 401
    )


def test_internal_auth_exchange_and_session_routes(monkeypatch) -> None:
    """Code exchange checks app scope and current workspace membership."""
    client = _client(monkeypatch)
    headers = {"X-Orcheo-App-Gateway-Token": "dedicated-secret"}
    descriptor = client.get(
        "/internal/hosted-apps/resolve?host=gateway-test.apps.test", headers=headers
    ).json()
    session = AppSession(
        app_id=UUID(descriptor["app_id"]),
        workspace_id=UUID(descriptor["workspace_id"]),
        secret_hash="a" * 64,
        app_host="gateway-test.apps.test",
        user_id="member-1",
        runtime_generation=int(descriptor["generation"]),
        expires_at=_utcnow() + timedelta(hours=1),
        idle_expires_at=_utcnow() + timedelta(minutes=30),
    )

    class FakeAuth:
        def __init__(self, issued_session: AppSession) -> None:
            self.issued_session = issued_session
            self.revoked: list[str] = []

        def exchange(self, **_kwargs):
            return SimpleNamespace(session=self.issued_session, secret="session-secret")

        def revoke(self, secret: str) -> None:
            self.revoked.append(secret)

    fake_auth = FakeAuth(session)
    monkeypatch.setattr(
        internal_routes, "get_app_auth_service", lambda _repo: fake_auth
    )
    monkeypatch.setattr(
        internal_routes,
        "get_workspace_repository",
        lambda: SimpleNamespace(get_membership=lambda *_: object()),
    )
    request = {
        "host": "gateway-test.apps.test",
        "code": "code",
        "verifier": "v" * 43,
        "redirect_uri": "https://gateway-test.apps.test/__orcheo/auth/callback",
    }
    assert client.post(
        "/internal/hosted-apps/auth/exchange", headers=headers, json=request
    ).json() == {"session_secret": "session-secret"}
    monkeypatch.setattr(
        internal_routes,
        "get_app_auth_service",
        lambda _repo: FakeAuth(session.model_copy(update={"app_id": uuid4()})),
    )
    assert (
        client.post(
            "/internal/hosted-apps/auth/exchange", headers=headers, json=request
        ).status_code
        == 401
    )
    monkeypatch.setattr(
        internal_routes,
        "get_app_auth_service",
        lambda _repo: fake_auth,
    )
    monkeypatch.setattr(
        internal_routes,
        "get_workspace_repository",
        lambda: SimpleNamespace(
            get_membership=lambda *_: (_ for _ in ()).throw(
                WorkspaceMembershipError("gone")
            )
        ),
    )
    assert (
        client.post(
            "/internal/hosted-apps/auth/exchange", headers=headers, json=request
        ).status_code
        == 401
    )
    monkeypatch.setattr(
        internal_routes,
        "_resolve_app_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppAuthError("bad session")),
    )
    assert client.get(
        "/internal/hosted-apps/auth/session",
        params={"host": "gateway-test.apps.test"},
        headers={**headers, "X-Orcheo-App-Session": "bad"},
    ).json() == {"authenticated": False}


def test_local_runtime_execution_and_scheduling_paths(monkeypatch) -> None:
    """Local execution verifies immutable evidence and settles accepted runs."""
    binding = AppBinding(
        workspace_id=uuid4(),
        app_id=uuid4(),
        name="lookup",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="0" * 64,
        access_mode="anonymous",
        runnable_config_snapshot={},
    )
    version = SimpleNamespace(
        workflow_id=binding.workflow_id,
        workspace_id=str(binding.workspace_id),
        graph={"nodes": []},
        runnable_config={},
        compute_checksum=lambda: "graph",
    )
    executable = {"graph_sha256": "graph", "runnable_config": {}}
    binding.workflow_execution_sha256 = hashlib.sha256(
        json.dumps(executable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    completed: list[dict] = []
    monkeypatch.setattr(
        internal_routes,
        "get_repository",
        lambda: SimpleNamespace(get_version=lambda _id: _async_value(version)),
    )
    monkeypatch.setattr(
        internal_routes,
        "get_app_runtime_service",
        lambda: SimpleNamespace(
            complete=lambda _handle, **kwargs: completed.append(kwargs)
        ),
    )
    monkeypatch.setattr(
        internal_routes,
        "execute_workflow_recorded",
        lambda *_args, **_kwargs: _async_value({"answer": "ok"}),
    )
    asyncio.run(
        internal_routes._execute_local_runtime_run(
            handle="handle", binding=binding, payload={}, execution_id=uuid4()
        )
    )
    assert completed[-1] == {"output": {"final_state": {"answer": "ok"}}}
    asyncio.run(
        internal_routes._execute_local_runtime_run(
            handle="handle", binding=binding, payload=[], execution_id=uuid4()
        )
    )
    assert "error" in completed[-1]
    monkeypatch.setattr(
        internal_routes,
        "get_repository",
        lambda: SimpleNamespace(
            get_version=lambda _id: _async_value(
                SimpleNamespace(
                    workflow_id=uuid4(),
                    workspace_id=str(binding.workspace_id),
                    graph={},
                    runnable_config={},
                    compute_checksum=lambda: "graph",
                )
            )
        ),
    )
    asyncio.run(
        internal_routes._execute_local_runtime_run(
            handle="handle", binding=binding, payload=[], execution_id=uuid4()
        )
    )
    assert "error" in completed[-1]
    monkeypatch.setattr(
        internal_routes,
        "get_repository",
        lambda: SimpleNamespace(get_version=lambda _id: _async_value(version)),
    )
    binding.workflow_execution_sha256 = "0" * 64
    asyncio.run(
        internal_routes._execute_local_runtime_run(
            handle="handle", binding=binding, payload={}, execution_id=uuid4()
        )
    )
    assert "error" in completed[-1]
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", "hosted")
    internal_routes._schedule_local_runtime_run(
        handle="h", binding=binding, payload={}, execution_id=uuid4()
    )
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", "local")
    scheduled: list[object] = []

    class FakeTask:
        def add_done_callback(self, callback) -> None:
            scheduled.append(callback)

    async def fake_execute(**_kwargs):
        return None

    monkeypatch.setattr(internal_routes, "_execute_local_runtime_run", fake_execute)
    monkeypatch.setattr(
        internal_routes.asyncio,
        "create_task",
        lambda coro: (coro.close(), FakeTask())[1],
    )
    internal_routes._schedule_local_runtime_run(
        handle="h", binding=binding, payload={}, execution_id=uuid4()
    )
    assert scheduled


async def _async_value(value):
    return value
