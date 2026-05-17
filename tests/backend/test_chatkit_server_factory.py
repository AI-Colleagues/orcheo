"""Tests for ChatKit server factory and configuration resolution."""

from __future__ import annotations
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from orcheo_backend.app.chatkit import server as chatkit_server_module
from orcheo_backend.app.chatkit.server import (
    _coerce_config_set,
    _resolve_chatkit_backend,
    _resolve_chatkit_pool_settings,
    _resolve_chatkit_postgres_dsn,
    create_chatkit_server,
)


class MockDynaconf(Mapping[str, Any]):
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Any:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def test_resolve_chatkit_backend_inputs() -> None:
    # Test Mapping
    assert _resolve_chatkit_backend({"CHATKIT_BACKEND": "postgres"}) == "postgres"
    assert _resolve_chatkit_backend({"chatkit_backend": "postgres"}) == "postgres"

    # Test Object
    assert (
        _resolve_chatkit_backend(SimpleNamespace(CHATKIT_BACKEND="postgres"))
        == "postgres"
    )
    assert (
        _resolve_chatkit_backend(SimpleNamespace(chatkit_backend="postgres"))
        == "postgres"
    )


def test_resolve_chatkit_backend_invalid() -> None:
    with pytest.raises(ValueError, match="must be 'postgres'"):
        _resolve_chatkit_backend({"CHATKIT_BACKEND": "invalid"})


def test_resolve_chatkit_postgres_dsn() -> None:
    # Test Mapping
    assert (
        _resolve_chatkit_postgres_dsn({"POSTGRES_DSN": "postgresql://test"})
        == "postgresql://test"
    )
    assert (
        _resolve_chatkit_postgres_dsn({"postgres_dsn": "postgresql://test"})
        == "postgresql://test"
    )

    # Test Object
    assert (
        _resolve_chatkit_postgres_dsn(SimpleNamespace(POSTGRES_DSN="postgresql://test"))
        == "postgresql://test"
    )
    assert (
        _resolve_chatkit_postgres_dsn(SimpleNamespace(postgres_dsn="postgresql://test"))
        == "postgresql://test"
    )

    # Test missing
    with pytest.raises(ValueError, match="must be set"):
        _resolve_chatkit_postgres_dsn({})


def test_resolve_chatkit_pool_settings() -> None:
    defaults = (1, 10, 30.0, 300.0)
    # Test defaults
    assert _resolve_chatkit_pool_settings({}) == defaults

    # Test Mapping
    settings = {
        "POSTGRES_POOL_MIN_SIZE": 2,
        "POSTGRES_POOL_MAX_SIZE": 20,
        "POSTGRES_POOL_TIMEOUT": 60.0,
        "POSTGRES_POOL_MAX_IDLE": 600.0,
    }
    assert _resolve_chatkit_pool_settings(settings) == (2, 20, 60.0, 600.0)

    # Test Object
    obj = SimpleNamespace(
        postgres_pool_min_size=2,
        postgres_pool_max_size=20,
        postgres_pool_timeout=60.0,
        postgres_pool_max_idle=600.0,
    )
    assert _resolve_chatkit_pool_settings(obj) == (2, 20, 60.0, 600.0)


def test_create_chatkit_server_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_postgres_store = MagicMock()
    monkeypatch.setattr(
        chatkit_server_module, "PostgresChatKitStore", mock_postgres_store
    )

    repository = MagicMock()
    vault_provider = MagicMock()

    settings = {
        "CHATKIT_BACKEND": "postgres",
        "POSTGRES_DSN": "postgresql://test",
    }

    with patch("orcheo_backend.app.chatkit.server.get_settings", return_value=settings):
        create_chatkit_server(repository, vault_provider)

    mock_postgres_store.assert_called_once()
    args, kwargs = mock_postgres_store.call_args
    assert args[0] == "postgresql://test"


def test_create_chatkit_server_rejects_non_postgres_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatKit server creation should reject non-postgres backends."""

    monkeypatch.setattr(
        chatkit_server_module,
        "_resolve_chatkit_backend",
        lambda settings: "sqlite",
    )
    monkeypatch.setattr(
        chatkit_server_module,
        "get_settings",
        lambda: {"CHATKIT_BACKEND": "postgres"},
    )

    with pytest.raises(ValueError, match="ChatKit backend must be 'postgres'"):
        create_chatkit_server(
            repository=MagicMock(),
            vault_provider=lambda: object(),
        )


def test_coerce_config_set() -> None:
    default = {"default"}
    assert _coerce_config_set(None, default) == default
    assert _coerce_config_set("a, b, c", default) == {"a", "b", "c"}
    assert _coerce_config_set(["a", "b"], default) == {"a", "b"}
    assert _coerce_config_set([], default) == default
    assert _coerce_config_set(" ", default) == default


def test_resolve_backend_dynaconf() -> None:
    class FakeDynaconf:
        def get(self, key: str, default: Any = None) -> Any:
            return None

    # We patch the class in the module so isinstance checks work
    with patch("orcheo_backend.app.chatkit.server.Dynaconf", FakeDynaconf):
        settings = FakeDynaconf()
        # Override get for this instance
        object.__setattr__(
            settings,
            "get",
            lambda k, d=None: "postgres" if k == "CHATKIT_BACKEND" else None,
        )

        assert _resolve_chatkit_backend(settings) == "postgres"


def test_resolve_chatkit_postgres_dsn_dynaconf() -> None:
    class FakeDynaconf:
        def get(self, key: str, default: Any = None) -> Any:
            return "postgresql://test" if key == "POSTGRES_DSN" else None

    with patch("orcheo_backend.app.chatkit.server.Dynaconf", FakeDynaconf):
        settings = FakeDynaconf()
        assert _resolve_chatkit_postgres_dsn(settings) == "postgresql://test"


def test_resolve_chatkit_pool_settings_dynaconf() -> None:
    class FakeDynaconf:
        def get(self, key: str, default: Any = None) -> Any:
            mapping = {
                "POSTGRES_POOL_MIN_SIZE": 5,
                "POSTGRES_POOL_MAX_SIZE": 50,
                "POSTGRES_POOL_TIMEOUT": 10.0,
                "POSTGRES_POOL_MAX_IDLE": 100.0,
            }
            return mapping.get(key, default)

    with patch("orcheo_backend.app.chatkit.server.Dynaconf", FakeDynaconf):
        settings = FakeDynaconf()
        assert _resolve_chatkit_pool_settings(settings) == (5, 50, 10.0, 100.0)
