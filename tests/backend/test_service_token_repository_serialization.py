"""Tests for service token repository serialization helpers."""

from __future__ import annotations
from datetime import UTC, datetime

from orcheo_backend.app.service_token_repository.serialization import (
    serialize_datetime,
    serialize_string_set,
)


def test_serialize_string_set_handles_empty_values() -> None:
    assert serialize_string_set(None) is None
    assert serialize_string_set(frozenset()) is None


def test_serialize_string_set_sorts_values() -> None:
    assert serialize_string_set(frozenset({"b", "a"})) == '["a", "b"]'


def test_serialize_datetime_handles_none_and_value() -> None:
    value = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert serialize_datetime(None) is None
    assert serialize_datetime(value) == "2024-01-02T03:04:05+00:00"
