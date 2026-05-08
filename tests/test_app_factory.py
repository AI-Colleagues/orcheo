"""Tests covering repository and FastAPI app factory helpers."""

import importlib
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock
import pytest
from fastapi import Request
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


def test_create_app_rejects_public_deployment_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App startup should fail fast for public deployments without auth."""

    monkeypatch.setenv("ORCHEO_CHATKIT_PUBLIC_BASE_URL", "https://canvas.example.com")
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_URL", "")
    monkeypatch.setenv("ORCHEO_AUTH_JWKS_STATIC", "")
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH", "")
    monkeypatch.setenv("ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN", "")
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "sqlite")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "optional")

    with pytest.raises(
        RuntimeError,
        match="Public deployment detected via CHATKIT_PUBLIC_BASE_URL",
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


def test_create_repository_inmemory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """The application factory instantiates the in-memory repository."""

    class DummySettings:
        repository_backend = "inmemory"
        repository_sqlite_path = "ignored.sqlite"

    monkeypatch.setattr(backend_module, "get_settings", lambda: DummySettings())

    repository = _create_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)


def test_create_repository_invalid_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported repository backends raise a clear error."""

    class DummySettings:
        repository_backend = "invalid_backend"
        repository_sqlite_path = "ignored.sqlite"

    monkeypatch.setattr(backend_module, "get_settings", lambda: DummySettings())

    with pytest.raises(ValueError, match="Unsupported repository backend"):
        _create_repository()


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
