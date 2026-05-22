"""Tests covering repository and FastAPI app factory helpers."""

import importlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from orcheo_backend.app import _create_repository, create_app, get_repository
from orcheo_backend.app._app_module import _AppModule, install_app_module_proxy
from orcheo_backend.app.authentication import AuthenticationError
from orcheo_backend.app.factory import (
    _DEFAULT_ALLOWED_ORIGINS,
    _authentication_error_handler,
    _load_allowed_origins,
)
from orcheo_backend.app.repository import InMemoryWorkflowRepository


backend_module = importlib.import_module("orcheo_backend.app")
dependencies_module = importlib.import_module("orcheo_backend.app.dependencies")
factory_module = importlib.import_module("orcheo_backend.app.factory")


def test_install_app_module_proxy_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installing the proxy twice should be a no-op on the second call."""
    module_name = "tests.dummy_app_module"
    dummy_module = ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, dummy_module)

    install_app_module_proxy(module_name)
    proxied_module = sys.modules[module_name]
    assert isinstance(proxied_module, _AppModule)

    install_app_module_proxy(module_name)
    assert sys.modules[module_name] is proxied_module

    sys.modules.pop(module_name, None)


def test_app_module_exposes_sensitive_debug_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module proxy forwards _should_log_sensitive_debug lookups."""
    sentinel = object()
    monkeypatch.setattr(
        backend_module._workflow_execution_module,  # type: ignore[attr-defined]
        "_should_log_sensitive_debug",
        sentinel,
        raising=False,
    )

    monkeypatch.delattr(backend_module, "_should_log_sensitive_debug", raising=False)

    assert backend_module._should_log_sensitive_debug is sentinel  # type: ignore[attr-defined]


def test_get_repository_returns_singleton() -> None:
    """The module-level repository accessor returns a singleton instance."""

    first = get_repository()
    second = get_repository()
    assert first is second


def test_create_app_allows_dependency_override() -> None:
    """Passing a repository instance wires it into FastAPI dependency overrides."""

    repository = InMemoryWorkflowRepository()
    app = create_app(repository)

    override = app.dependency_overrides[get_repository]
    assert override() is repository


def test_configure_dependency_overrides_applies_all_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_configure_dependency_overrides wires each optional singleton override."""

    app = FastAPI()
    repository = object()
    history_store = object()
    checkpoint_store = object()
    plugin_store = object()
    vault = object()
    credential_service = type("CredentialService", (), {"_vault": vault})()

    monkeypatch.setitem(dependencies_module._repository_ref, "repository", None)
    monkeypatch.setitem(dependencies_module._history_store_ref, "store", None)
    monkeypatch.setitem(dependencies_module._checkpoint_store_ref, "store", None)
    monkeypatch.setitem(
        dependencies_module._plugin_installation_store_ref, "store", None
    )
    monkeypatch.setitem(dependencies_module._credential_service_ref, "service", None)
    monkeypatch.setitem(dependencies_module._vault_ref, "vault", None)

    listener_runtime_store = factory_module._configure_dependency_overrides(
        app,
        repository,
        history_store=history_store,
        checkpoint_store=checkpoint_store,
        plugin_installation_store=plugin_store,
        credential_service=credential_service,
    )

    assert dependencies_module._repository_ref["repository"] is repository
    assert dependencies_module._history_store_ref["store"] is history_store
    assert dependencies_module._checkpoint_store_ref["store"] is checkpoint_store
    assert dependencies_module._plugin_installation_store_ref["store"] is plugin_store
    assert dependencies_module._credential_service_ref["service"] is credential_service
    assert dependencies_module._vault_ref["vault"] is vault
    assert app.dependency_overrides[dependencies_module.get_repository]() is repository
    assert (
        app.dependency_overrides[dependencies_module.get_history_store]()
        is history_store
    )
    assert (
        app.dependency_overrides[dependencies_module.get_checkpoint_store]()
        is checkpoint_store
    )
    assert (
        app.dependency_overrides[dependencies_module.get_plugin_installation_store]()
        is plugin_store
    )
    assert (
        app.dependency_overrides[dependencies_module.get_credential_service]()
        is credential_service
    )
    assert app.state.listener_runtime_store is listener_runtime_store


def test_create_app_rejects_public_deployment_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App startup should fail fast for public deployments without auth."""

    monkeypatch.setenv("ORCHEO_STUDIO_URL", "https://canvas.example.com")
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_URL", "")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_STATIC", "")
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH", "")
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", "")
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "optional")

    with pytest.raises(
        RuntimeError,
        match="Public deployment detected via STUDIO_URL",
    ):
        with TestClient(create_app(InMemoryWorkflowRepository())):
            pass


def test_create_app_lifespan_runs_startup_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app lifespan should initialize and tear down the listener runtime."""

    events: list[object] = []
    repository = InMemoryWorkflowRepository()
    sentinel_vault = object()
    sentinel_runtime_store = object()

    class FakeListenerRuntime:
        def __init__(self, repository: object, vault: object, runtime_store: object):
            events.append(("init", repository, vault, runtime_store))

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(
        factory_module,
        "load_auth_settings",
        lambda refresh=True: events.append(("load_auth", refresh)),
    )
    monkeypatch.setattr(
        factory_module,
        "load_enabled_plugins",
        lambda force=True: events.append(("load_plugins", force)),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_managed_vibe_workflow",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module, "get_chatkit_server", lambda: events.append("chatkit")
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "cancel_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(factory_module, "ListenerRuntimeService", FakeListenerRuntime)
    monkeypatch.setattr(factory_module, "get_vault", lambda: sentinel_vault)
    monkeypatch.setattr(
        factory_module,
        "get_listener_runtime_store",
        lambda: sentinel_runtime_store,
    )
    monkeypatch.setattr(factory_module, "get_repository", lambda: repository)

    app = create_app(repository)

    with TestClient(app):
        pass

    assert ("load_auth", True) in events
    assert ("load_plugins", True) in events
    assert any(event[0] == "init" for event in events if isinstance(event, tuple))
    assert "start" in events
    assert "stop" in events


def test_create_app_lifespan_ignores_chatkit_startup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup exception branch should be swallowed and still stop cleanly."""

    events: list[object] = []
    repository = InMemoryWorkflowRepository()
    sentinel_vault = object()
    sentinel_runtime_store = object()

    class FakeListenerRuntime:
        def __init__(self, repository: object, vault: object, runtime_store: object):
            events.append(("init", repository, vault, runtime_store))

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(factory_module, "load_auth_settings", lambda refresh=True: None)
    monkeypatch.setattr(factory_module, "load_enabled_plugins", lambda force=True: None)
    monkeypatch.setattr(
        factory_module,
        "ensure_managed_vibe_workflow",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "get_chatkit_server",
        lambda: (_ for _ in ()).throw(RuntimeError("chatkit offline")),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "cancel_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(factory_module, "ListenerRuntimeService", FakeListenerRuntime)
    monkeypatch.setattr(factory_module, "get_vault", lambda: sentinel_vault)
    monkeypatch.setattr(
        factory_module,
        "get_listener_runtime_store",
        lambda: sentinel_runtime_store,
    )
    monkeypatch.setattr(factory_module, "get_repository", lambda: repository)

    app = create_app(repository)

    with TestClient(app):
        pass

    assert "start" in events
    assert "stop" in events


@pytest.mark.asyncio
async def test_app_lifespan_bootstraps_repository_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifespan should create the repository when the singleton is empty."""

    events: list[object] = []
    sentinel_repository = object()
    sentinel_vault = object()
    sentinel_runtime_store = object()

    class FakeListenerRuntime:
        def __init__(self, repository: object, vault: object, runtime_store: object):
            events.append(("init", repository, vault, runtime_store))

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    class FakeWorkspaceService:
        def list_workspaces(self, include_inactive: bool) -> list[object]:
            events.append(("workspaces", include_inactive))
            return [object()]

    monkeypatch.setitem(factory_module._repository_ref, "repository", None)

    def stub_create_repository() -> object:
        factory_module._repository_ref["repository"] = sentinel_repository
        return sentinel_repository

    monkeypatch.setattr(factory_module, "_create_repository", stub_create_repository)
    monkeypatch.setattr(
        "orcheo.tracing.configure_tracing",
        lambda: events.append("tracing"),
    )
    monkeypatch.setattr(
        factory_module,
        "load_auth_settings",
        lambda refresh=True: events.append(("load_auth", refresh)),
    )
    monkeypatch.setattr(
        factory_module,
        "load_enabled_plugins",
        lambda force=True: events.append(("load_plugins", force)),
    )
    monkeypatch.setattr(
        factory_module,
        "get_workspace_service",
        lambda: FakeWorkspaceService(),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_managed_vibe_workflow",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(factory_module, "get_repository", lambda: sentinel_repository)
    monkeypatch.setattr(factory_module, "get_vault", lambda: sentinel_vault)
    monkeypatch.setattr(
        factory_module,
        "get_listener_runtime_store",
        lambda: sentinel_runtime_store,
    )
    monkeypatch.setattr(factory_module, "ListenerRuntimeService", FakeListenerRuntime)
    monkeypatch.setattr(
        factory_module,
        "get_chatkit_server",
        lambda: events.append("chatkit"),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "cancel_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )

    app = FastAPI()

    async with factory_module._app_lifespan(app):
        assert app.state.listener_runtime is not None

    assert ("load_auth", True) in events
    assert ("load_plugins", True) in events
    assert ("workspaces", False) in events
    assert "tracing" in events
    assert "chatkit" in events
    assert any(event[0] == "init" for event in events if isinstance(event, tuple))
    assert "start" in events
    assert "stop" in events
    assert factory_module.ensure_managed_vibe_workflow.await_count == 1
    assert factory_module._repository_ref["repository"] is sentinel_repository


def test_create_repository_postgres_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The application factory delegates repository creation to postgres wiring."""

    class DummySettings:
        repository_backend = "postgres"

    monkeypatch.setattr(backend_module, "get_settings", lambda: DummySettings())
    sentinel_service = object()
    sentinel_repository = object()
    monkeypatch.setattr(
        dependencies_module,
        "_ensure_credential_service",
        lambda settings=None: sentinel_service,  # noqa: ARG005
    )
    monkeypatch.setattr(
        dependencies_module,
        "create_repository",
        lambda *args, **kwargs: sentinel_repository,  # noqa: ARG005
    )

    repository = _create_repository()
    assert repository is sentinel_repository


def test_create_repository_invalid_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported repository backends raise a clear error."""

    class DummySettings:
        repository_backend = "invalid_backend"

    monkeypatch.setattr(backend_module, "get_settings", lambda: DummySettings())

    with pytest.raises(ValueError, match="Repository backend must be 'postgres'"):
        _create_repository()


@pytest.mark.asyncio
async def test_robots_txt_returns_crawl_policy() -> None:
    """The robots.txt endpoint should expose the crawl policy."""

    response = await factory_module._robots_txt()

    assert response.status_code == 200
    assert response.body == b"User-agent: *\nDisallow: /\n"


def test_load_allowed_origins_reads_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON arrays should be parsed, trimmed, and filtered."""
    monkeypatch.setenv(
        "ORCHEO_CORS_ALLOW_ORIGINS",
        json.dumps([" https://foo.example ", ""]),
    )

    origins = _load_allowed_origins()
    assert origins == ["https://foo.example"]


def test_load_allowed_origins_reads_csv_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated values fallback when JSON parsing fails."""
    monkeypatch.setenv(
        "ORCHEO_CORS_ALLOW_ORIGINS",
        "https://a.example, ,https://b.example  ",
    )

    origins = _load_allowed_origins()
    assert origins == ["https://a.example", "https://b.example"]


def test_load_allowed_origins_defaults_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the filtered list is empty the defaults should be returned."""
    monkeypatch.setenv(
        "ORCHEO_CORS_ALLOW_ORIGINS",
        json.dumps(["", "   "]),
    )

    origins = _load_allowed_origins()
    assert origins == list(_DEFAULT_ALLOWED_ORIGINS)
    assert origins is not _DEFAULT_ALLOWED_ORIGINS


def test_load_allowed_origins_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var is unset, the defaults should be returned."""
    monkeypatch.delenv("ORCHEO_CORS_ALLOW_ORIGINS", raising=False)

    origins = _load_allowed_origins()
    assert origins == list(_DEFAULT_ALLOWED_ORIGINS)
    assert origins is not _DEFAULT_ALLOWED_ORIGINS


@pytest.mark.asyncio
async def test_authentication_error_handler_maps_to_http_response() -> None:
    """Authentication errors should map to FastAPI's HTTP exception handler."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    error = AuthenticationError("missing token")

    response = await _authentication_error_handler(request, error)

    assert response.status_code == 401


def test_build_credential_broker_resolver_returns_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inner _resolve_credential finds and returns the matching credential."""
    from orcheo_backend.app import sandbox as sandbox_module
    from orcheo_backend.app.sandbox import build_credential_broker

    cred_id = "cred-uuid-1"
    fake_metadata = SimpleNamespace(name="my-secret", id=cred_id)
    fake_vault = SimpleNamespace(
        list_credentials=lambda context, workspace_id: [fake_metadata],
        reveal_secret=lambda credential_id, context: "super-secret-value",
    )
    monkeypatch.setattr(sandbox_module, "get_vault", lambda: fake_vault)
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "test-secret")

    broker = build_credential_broker()
    token = broker.issue(
        workspace_id="00000000-0000-0000-0000-000000000001", run_id="r1"
    )
    _, value = broker.resolve(token, credential_name="my-secret")

    assert value == "super-secret-value"


def test_build_credential_broker_resolver_raises_key_error_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inner _resolve_credential raises KeyError when no credential matches name."""
    from orcheo.sandbox.broker import BrokerScopeError
    from orcheo_backend.app import sandbox as sandbox_module
    from orcheo_backend.app.sandbox import build_credential_broker

    # Vault has one credential, but it does NOT match the requested name.
    # This exercises the `if metadata.name == credential_name:` False branch.
    other_cred = SimpleNamespace(name="other-secret", id="cred-id-other")
    fake_vault = SimpleNamespace(
        list_credentials=lambda context, workspace_id: [other_cred],
        reveal_secret=lambda credential_id, context: "other-value",
    )
    monkeypatch.setattr(sandbox_module, "get_vault", lambda: fake_vault)
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "test-secret")

    broker = build_credential_broker()
    token = broker.issue(
        workspace_id="00000000-0000-0000-0000-000000000001", run_id="r2"
    )

    with pytest.raises(BrokerScopeError):
        broker.resolve(token, credential_name="nonexistent")
