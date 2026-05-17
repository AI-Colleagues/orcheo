"""Unit tests for settings helper utilities in ``orcheo_backend.app``."""

from __future__ import annotations
from types import SimpleNamespace
from orcheo_backend.app import _settings_value


def test_settings_value_returns_default_when_attribute_missing() -> None:
    """Accessing a missing attribute path falls back to the provided default."""

    settings = SimpleNamespace(vault=SimpleNamespace())

    value = _settings_value(
        settings,
        attr_path="vault.backend",
        env_key="VAULT_BACKEND",
        default="inmemory",
    )

    assert value == "inmemory"


def test_settings_value_reads_nested_attribute() -> None:
    """Nested attribute paths return the stored value when present."""

    settings = SimpleNamespace(vault=SimpleNamespace(token=SimpleNamespace(ttl=60)))

    value = _settings_value(
        settings,
        attr_path="vault.token.ttl",
        env_key="VAULT_TOKEN_TTL",
        default=30,
    )

    assert value == 60


def test_settings_value_prefers_mapping_get() -> None:
    """Mapping-like settings use the ``get`` method when available."""

    settings = {"VAULT_BACKEND": "postgres"}
    value = _settings_value(
        settings,
        attr_path="vault.backend",
        env_key="VAULT_BACKEND",
        default="inmemory",
    )

    assert value == "postgres"


def test_settings_value_without_attr_path_returns_default() -> None:
    value = _settings_value({}, attr_path=None, env_key="MISSING", default=42)
    assert value == 42


def test_settings_value_handles_missing_root_attribute() -> None:
    settings = SimpleNamespace()
    value = _settings_value(
        settings,
        attr_path="vault.backend",
        env_key="VAULT_BACKEND",
        default="fallback",
    )
    assert value == "fallback"
