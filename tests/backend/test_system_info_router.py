"""Tests for the /api/system/info endpoint."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
import httpx
import pytest
from orcheo.plugins import PluginLoadReport, PluginLoadResult
from orcheo_backend.app import plugin_inventory, versioning
from orcheo_backend.app.routers import system as system_router
from tests.backend.authentication_test_utils import create_test_client, reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable auth and reset global state between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset versioning cache between tests."""

    versioning._cache_state["entry"] = None  # type: ignore[attr-defined]


def test_system_info_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint returns a successful payload with version data."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")

    monkeypatch.setattr(
        versioning,
        "_read_current_version",
        lambda package: {
            "orcheo-backend": "0.1.0",
            "orcheo-sdk": "0.2.0",
            "orcheo-canvas": "0.3.0",
        }.get(package),
    )
    monkeypatch.setattr(
        versioning, "_fetch_pypi_latest", lambda *args, **kwargs: "0.5.0"
    )
    monkeypatch.setattr(
        versioning, "_fetch_npm_latest", lambda *args, **kwargs: "0.4.0"
    )

    client = create_test_client()
    response = client.get("/api/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"]["current_version"] == "0.1.0"
    assert payload["backend"]["latest_version"] == "0.5.0"
    assert payload["backend"]["update_available"] is True
    assert payload["cli"]["latest_version"] == "0.5.0"
    assert payload["canvas"]["latest_version"] == "0.4.0"
    assert "checked_at" in payload


def test_system_info_registry_failure_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint soft-fails registry lookup and returns null latest versions."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")

    monkeypatch.setattr(
        versioning,
        "_read_current_version",
        lambda package: {
            "orcheo-backend": "0.1.0",
            "orcheo-sdk": "0.2.0",
            "orcheo-canvas": "0.3.0",
        }.get(package),
    )
    monkeypatch.setattr(versioning, "_fetch_pypi_latest", lambda *args, **kwargs: None)
    monkeypatch.setattr(versioning, "_fetch_npm_latest", lambda *args, **kwargs: None)

    client = create_test_client()
    response = client.get("/api/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"]["latest_version"] is None
    assert payload["backend"]["update_available"] is False
    assert payload["cli"]["latest_version"] is None
    assert payload["canvas"]["latest_version"] is None


def test_system_info_reads_canvas_current_version_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_CANVAS_VERSION", "1.2.3")

    monkeypatch.setattr(
        versioning,
        "_read_current_version",
        lambda package: {
            "orcheo-backend": "0.1.0",
            "orcheo-sdk": "0.2.0",
        }.get(package),
    )
    monkeypatch.setattr(versioning, "_fetch_pypi_latest", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        versioning, "_fetch_npm_latest", lambda *args, **kwargs: "1.2.4"
    )

    client = create_test_client()
    response = client.get("/api/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["canvas"]["current_version"] == "1.2.3"
    assert payload["canvas"]["latest_version"] == "1.2.4"
    assert payload["canvas"]["update_available"] is True


def test_system_health_is_public_when_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health endpoint remains available without auth tokens."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")

    client = create_test_client()
    health_response = client.get("/api/system/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}


def test_system_plugins_reports_backend_process_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin inventory includes enabled state plus current-process load status."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setattr(
        plugin_inventory,
        "PluginManager",
        lambda: type(
            "_Manager",
            (),
            {
                "list_plugins": staticmethod(
                    lambda: [
                        {
                            "name": "orcheo-plugin-lark-listener",
                            "enabled": True,
                            "status": "installed",
                            "version": "0.1.0",
                            "exports": ["nodes", "listeners"],
                            "source": "orcheo-plugin-lark-listener",
                        }
                    ]
                )
            },
        )(),
    )
    monkeypatch.setattr(
        plugin_inventory,
        "load_enabled_plugins",
        lambda *, force=False: PluginLoadReport(
            generation=1,
            results=[
                PluginLoadResult(
                    name="orcheo-plugin-lark-listener",
                    loaded=True,
                )
            ],
        ),
    )

    client = create_test_client()
    response = client.get("/api/system/plugins")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plugins"] == [
        {
            "name": "orcheo-plugin-lark-listener",
            "enabled": True,
            "status": "installed",
            "version": "0.1.0",
            "exports": ["nodes", "listeners"],
            "loaded": True,
            "load_error": None,
        }
    ]


@pytest.mark.asyncio()
async def test_system_plugins_applies_workspace_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-specific plugin states should override runtime defaults."""

    monkeypatch.setattr(
        system_router,
        "list_runtime_plugins",
        lambda: [
            {
                "name": "alpha",
                "enabled": False,
                "status": "installed",
                "version": "1.0.0",
                "exports": ["nodes"],
                "loaded": False,
                "load_error": None,
            },
            {
                "name": "beta",
                "enabled": True,
                "status": "installed",
                "version": "1.0.0",
                "exports": ["nodes"],
                "loaded": True,
                "load_error": None,
            },
        ],
    )

    class PluginState:
        def __init__(self, plugin_name: str, enabled: bool) -> None:
            self.plugin_name = plugin_name
            self.enabled = enabled

    class PluginStore:
        async def list_plugin_states(self, *, workspace_id: str):
            assert workspace_id == "workspace-1"
            return [PluginState("alpha", True)]

    response = await system_router.get_system_plugins(
        workspace=type("W", (), {"workspace_id": "workspace-1"})(),
        plugin_store=PluginStore(),
    )

    assert response.plugins[0].enabled is True
    assert response.plugins[1].enabled is True


def test_robots_txt_is_public_when_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Robots endpoint remains available without auth tokens."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")

    client = create_test_client()
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "User-agent: *\nDisallow: /\n"


def test_versioning_private_helpers_cover_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert versioning._is_stable(None) is False
    assert versioning._is_stable("not-a-version") is False
    assert versioning._safe_parse("bad-version") is None
    assert versioning._update_available("1.0.0rc1", "1.0.0") is False

    def _missing(_name: str) -> str:
        raise PackageNotFoundError("missing")

    monkeypatch.setattr(versioning, "package_version", _missing)
    assert versioning._read_current_version("missing") is None


def test_versioning_registry_fetch_helpers_cover_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(url: str, timeout: float) -> object:
        del url, timeout
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(versioning.httpx, "get", _raise)
    with pytest.raises(httpx.ConnectError):
        versioning._fetch_json("https://example.test", timeout=1.0, retries=0)

    monkeypatch.setattr(
        versioning,
        "_fetch_json",
        lambda _url, *, timeout, retries: {"info": {"version": "1.2.3"}},
    )
    assert (
        versioning._fetch_pypi_latest("orcheo-sdk", timeout=1.0, retries=0) == "1.2.3"
    )
    monkeypatch.setattr(
        versioning,
        "_fetch_json",
        lambda _url, *, timeout, retries: {"info": {"version": 123}},
    )
    assert versioning._fetch_pypi_latest("orcheo-sdk", timeout=1.0, retries=0) is None
    monkeypatch.setattr(
        versioning, "_fetch_json", lambda _url, *, timeout, retries: {"info": "bad"}
    )
    assert versioning._fetch_pypi_latest("orcheo-sdk", timeout=1.0, retries=0) is None
    monkeypatch.setattr(
        versioning,
        "_fetch_json",
        lambda _url, *, timeout, retries: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert versioning._fetch_pypi_latest("orcheo-sdk", timeout=1.0, retries=0) is None

    monkeypatch.setattr(
        versioning,
        "_fetch_json",
        lambda _url, *, timeout, retries: {"version": "9.9.9"},
    )
    assert (
        versioning._fetch_npm_latest("orcheo-canvas", timeout=1.0, retries=0) == "9.9.9"
    )
    monkeypatch.setattr(
        versioning, "_fetch_json", lambda _url, *, timeout, retries: {"version": 1}
    )
    assert versioning._fetch_npm_latest("orcheo-canvas", timeout=1.0, retries=0) is None
    monkeypatch.setattr(
        versioning,
        "_fetch_json",
        lambda _url, *, timeout, retries: (_ for _ in ()).throw(httpx.HTTPError("bad")),
    )
    assert versioning._fetch_npm_latest("orcheo-canvas", timeout=1.0, retries=0) is None


def test_fetch_json_accepts_dict_and_rejects_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return {"ok": True}

    monkeypatch.setattr(versioning.httpx, "get", lambda url, timeout: _Response())
    assert versioning._fetch_json("https://example.test", timeout=1.0, retries=0) == {
        "ok": True
    }

    class _ListResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return ["not", "a", "dict"]

    monkeypatch.setattr(versioning.httpx, "get", lambda url, timeout: _ListResponse())
    with pytest.raises(ValueError, match="Unexpected registry payload"):
        versioning._fetch_json("https://example.test", timeout=1.0, retries=0)


def test_versioning_timeout_retries_and_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS", raising=False)
    assert versioning._read_timeout_seconds() == 3.0
    monkeypatch.setenv("ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS", "abc")
    assert versioning._read_timeout_seconds() == 3.0
    monkeypatch.setenv("ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS", "-1")
    assert versioning._read_timeout_seconds() == 3.0

    monkeypatch.delenv("ORCHEO_UPDATE_CHECK_RETRIES", raising=False)
    assert versioning._read_retries() == 1
    monkeypatch.setenv("ORCHEO_UPDATE_CHECK_RETRIES", "abc")
    assert versioning._read_retries() == 1
    monkeypatch.setenv("ORCHEO_UPDATE_CHECK_RETRIES", "-2")
    assert versioning._read_retries() == 1

    cached = {"ok": True}
    versioning._cache_state["entry"] = versioning._CacheEntry(  # type: ignore[attr-defined]
        payload=cached,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        versioning,
        "_build_payload",
        lambda: (_ for _ in ()).throw(RuntimeError("must not build")),
    )
    assert versioning.get_system_info_payload() == cached
