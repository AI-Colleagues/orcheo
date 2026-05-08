"""Unit tests for AppSettings widget set coercion helpers."""

import pytest
from orcheo.config.app_settings import AppSettings
from orcheo.config.defaults import _DEFAULTS


def test_coerce_widget_set_returns_defaults_for_invalid_inputs() -> None:
    expected = set(_DEFAULTS["CHATKIT_WIDGET_TYPES"])

    assert AppSettings._coerce_widget_set(None, "CHATKIT_WIDGET_TYPES") == expected
    assert AppSettings._coerce_widget_set(object(), "CHATKIT_WIDGET_TYPES") == expected


def test_coerce_widget_set_parses_strings_and_iterables() -> None:
    default_actions = set(_DEFAULTS["CHATKIT_WIDGET_ACTION_TYPES"])

    assert AppSettings._coerce_widget_set("custom, Card ", "CHATKIT_WIDGET_TYPES") == {
        "custom",
        "Card",
    }
    assert (
        AppSettings._coerce_widget_set("   ", "CHATKIT_WIDGET_ACTION_TYPES")
        == default_actions
    )
    assert AppSettings._coerce_widget_set(["Card", ""], "CHATKIT_WIDGET_TYPES") == {
        "Card"
    }
    assert AppSettings._coerce_widget_set(
        ("Action", " "), "CHATKIT_WIDGET_ACTION_TYPES"
    ) == {"Action"}
    assert AppSettings._coerce_widget_set(
        frozenset({"ListView"}), "CHATKIT_WIDGET_TYPES"
    ) == {"ListView"}


def test_coerce_widget_set_reverts_to_defaults_for_empty_collections() -> None:
    default = set(_DEFAULTS["CHATKIT_WIDGET_TYPES"])

    assert AppSettings._coerce_widget_set([], "CHATKIT_WIDGET_TYPES") == default
    assert AppSettings._coerce_widget_set((), "CHATKIT_WIDGET_TYPES") == default


def test_coerce_postgres_pool_int_defaults_and_valid_values() -> None:
    """Test _coerce_postgres_pool_int handles various input types."""
    assert AppSettings._coerce_postgres_pool_int(None, "POSTGRES_POOL_MIN_SIZE") == 1
    assert AppSettings._coerce_postgres_pool_int(None, "POSTGRES_POOL_MAX_SIZE") == 10
    assert AppSettings._coerce_postgres_pool_int(5, "POSTGRES_POOL_MIN_SIZE") == 5
    assert AppSettings._coerce_postgres_pool_int("10", "POSTGRES_POOL_MAX_SIZE") == 10


def test_coerce_postgres_pool_float_defaults_and_valid_values() -> None:
    """Test _coerce_postgres_pool_float handles various input types."""
    assert (
        AppSettings._coerce_postgres_pool_float(None, "POSTGRES_POOL_TIMEOUT") == 30.0
    )
    assert (
        AppSettings._coerce_postgres_pool_float(None, "POSTGRES_POOL_MAX_IDLE") == 300.0
    )
    assert AppSettings._coerce_postgres_pool_float(60, "POSTGRES_POOL_TIMEOUT") == 60.0
    assert (
        AppSettings._coerce_postgres_pool_float(45.5, "POSTGRES_POOL_MAX_IDLE") == 45.5
    )
    assert (
        AppSettings._coerce_postgres_pool_float("120.5", "POSTGRES_POOL_TIMEOUT")
        == 120.5
    )


def test_coerce_graph_store_backend_defaults_and_valid_values() -> None:
    """Graph store backend coercion should mirror checkpoint backend behavior."""

    assert AppSettings._coerce_graph_store_backend(None) == "sqlite"
    assert AppSettings._coerce_graph_store_backend("POSTGRES") == "postgres"


def test_coerce_workspace_backend_defaults_and_validation() -> None:
    """Workspace backend coercion should accept supported values only."""

    assert AppSettings._coerce_workspace_backend(None) == "inmemory"
    assert AppSettings._coerce_workspace_backend("POSTGRES") == "postgres"

    with pytest.raises(ValueError, match="ORCHEO_WORKSPACE_BACKEND"):
        AppSettings._coerce_workspace_backend("unsupported")


def test_coerce_chatkit_and_postgres_pool_helpers_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="CHATKIT_MAX_UPLOAD_SIZE_BYTES"):
        AppSettings._coerce_chatkit_max_upload_size_bytes("bad")
    with pytest.raises(ValueError, match="CHATKIT_RETENTION_DAYS"):
        AppSettings._coerce_chatkit_retention("bad")
    with pytest.raises(ValueError, match="PostgreSQL pool size"):
        AppSettings._coerce_postgres_pool_int("bad", "POSTGRES_POOL_MIN_SIZE")
    with pytest.raises(ValueError, match="PostgreSQL pool timeout"):
        AppSettings._coerce_postgres_pool_float("bad", "POSTGRES_POOL_TIMEOUT")


def test_coerce_port_and_tracing_helpers_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="ORCHEO_PORT"):
        AppSettings._parse_port("bad")
    with pytest.raises(ValueError, match="ORCHEO_TRACING_SAMPLE_RATIO"):
        AppSettings._coerce_tracing_sample_ratio("bad")
    assert AppSettings._coerce_tracing_high_token_threshold(True) == 1
    with pytest.raises(ValueError, match="TRACING_HIGH_TOKEN_THRESHOLD"):
        AppSettings._coerce_tracing_high_token_threshold("bad")
    assert AppSettings._coerce_tracing_preview_max_length(False) == 0
    with pytest.raises(ValueError, match="TRACING_PREVIEW_MAX_LENGTH"):
        AppSettings._coerce_tracing_preview_max_length("bad")


def test_apply_runtime_defaults_restores_invalid_retention_days() -> None:
    settings = AppSettings()
    settings.chatkit_retention_days = 0
    settings._apply_runtime_defaults()

    assert settings.chatkit_retention_days == _DEFAULTS["CHATKIT_RETENTION_DAYS"]
