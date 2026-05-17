"""Authentication parser tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime
import pytest
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_parse_max_age_from_cache_control() -> None:
    """_parse_max_age extracts max-age from Cache-Control headers."""
    from orcheo_backend.app.authentication import _parse_max_age

    assert _parse_max_age("max-age=300") == 300
    assert _parse_max_age("public, max-age=600, must-revalidate") == 600
    assert _parse_max_age("no-cache") is None
    assert _parse_max_age(None) is None


def test_parse_timestamp_from_various_formats() -> None:
    """_parse_timestamp handles multiple timestamp formats."""
    from orcheo_backend.app.authentication import _parse_timestamp

    now = datetime.now(tz=UTC)
    timestamp = int(now.timestamp())

    # Unix timestamp (int)
    result = _parse_timestamp(timestamp)
    assert result is not None
    assert abs((result - now).total_seconds()) < 1

    # Unix timestamp (string)
    result = _parse_timestamp(str(timestamp))
    assert result is not None

    # ISO format
    iso_string = now.isoformat()
    result = _parse_timestamp(iso_string)
    assert result is not None

    # None
    assert _parse_timestamp(None) is None


def test_parse_int_with_various_types() -> None:
    """_parse_int handles multiple input types."""
    from orcheo_backend.app.authentication import _parse_int

    assert _parse_int(42, 0) == 42
    assert _parse_int("100", 0) == 100
    assert _parse_int(None, 99) == 99


def test_parse_float_with_various_types() -> None:
    """_parse_float handles multiple input types."""
    from orcheo_backend.app.authentication import _parse_float

    assert _parse_float(3.14, 0.0) == 3.14
    assert _parse_float("2.5", 0.0) == 2.5
    assert _parse_float(None, 1.5) == 1.5


def test_coerce_mode_with_valid_values() -> None:
    """_coerce_mode returns valid mode strings."""
    from orcheo_backend.app.authentication import _coerce_mode

    assert _coerce_mode("disabled") == "disabled"
    assert _coerce_mode("required") == "required"
    assert _coerce_mode("optional") == "optional"
    assert _coerce_mode("REQUIRED") == "required"  # case insensitive
    assert _coerce_mode("invalid") == "optional"  # default
    assert _coerce_mode(123) == "optional"  # non-string


def test_parse_str_sequence() -> None:
    """_parse_str_sequence converts values to string tuples."""
    from orcheo_backend.app.authentication import _parse_str_sequence

    result = _parse_str_sequence(["item1", "item2", "item3"])
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_coerce_mode_backend_with_valid_values() -> None:
    """_coerce_mode_backend returns valid backend strings."""
    from orcheo_backend.app.authentication import _coerce_mode_backend

    assert _coerce_mode_backend("postgres") == "postgres"
    assert _coerce_mode_backend("POSTGRES") == "postgres"  # case insensitive

    with pytest.raises(ValueError, match="ORCHEO_AUTH_SERVICE_TOKEN_BACKEND"):
        _coerce_mode_backend("inmemory")
    with pytest.raises(ValueError, match="ORCHEO_AUTH_SERVICE_TOKEN_BACKEND"):
        _coerce_mode_backend("invalid")
    with pytest.raises(ValueError, match="ORCHEO_AUTH_SERVICE_TOKEN_BACKEND"):
        _coerce_mode_backend(123)


def test_coerce_optional_str() -> None:
    """_coerce_optional_str handles None and empty strings."""
    from orcheo_backend.app.authentication import _coerce_optional_str

    assert _coerce_optional_str("value") == "value"
    assert _coerce_optional_str("  ") is None
    assert _coerce_optional_str(None) is None
    assert _coerce_optional_str(123) == "123"


def test_parse_timestamp_with_invalid_string() -> None:
    """_parse_timestamp returns None for invalid string formats."""
    from orcheo_backend.app.authentication import _parse_timestamp

    # Invalid ISO format
    assert _parse_timestamp("not-a-date") is None

    # Non-digit, non-ISO string
    assert _parse_timestamp("2025-99-99") is None


def test_parse_timestamp_with_iso_z_format() -> None:
    """_parse_timestamp handles ISO format with Z suffix."""
    from orcheo_backend.app.authentication import _parse_timestamp

    result = _parse_timestamp("2025-01-01T12:00:00Z")

    assert result is not None
    assert result.year == 2025


def test_parse_timestamp_with_float() -> None:
    """_parse_timestamp handles float timestamps."""
    from orcheo_backend.app.authentication import _parse_timestamp

    now = datetime.now(tz=UTC)
    timestamp = now.timestamp()

    result = _parse_timestamp(timestamp)

    assert result is not None
    assert abs((result - now).total_seconds()) < 1


def test_parse_timestamp_returns_none_for_invalid_types() -> None:
    """_parse_timestamp returns None for types that can't be converted."""
    from orcheo_backend.app.authentication import _parse_timestamp

    # Object type that's not int/float/str
    result = _parse_timestamp(object())
    assert result is None
