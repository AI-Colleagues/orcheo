"""Tests for inline configurable-schema resolution."""

from __future__ import annotations
import pytest
from orcheo.runtime.configurable_schema import (
    ConfigurableSchemaError,
    is_schema_declaration,
    split_configurable,
)


def test_is_schema_declaration_detects_inline_annotation() -> None:
    """A mapping with a schema key and a discriminator is a declaration."""
    assert (
        is_schema_declaration({"type": "string", "enum": ["a", "b"], "default": "a"})
        is True
    )


def test_is_schema_declaration_rejects_non_mapping() -> None:
    """Plain runtime values are never schema declarations."""
    assert is_schema_declaration("openai:gpt-4.1-mini") is False


def test_is_schema_declaration_rejects_mapping_without_schema_keys() -> None:
    """A mapping carrying no JSON Schema keys stays a runtime value."""
    assert is_schema_declaration({"label": "Model"}) is False


def test_is_schema_declaration_requires_a_discriminator_key() -> None:
    """Ambiguous schema keys alone are not enough to flag a declaration."""
    assert is_schema_declaration({"type": "provider", "pattern": "^openai"}) is False


def test_split_configurable_resolves_default_const_and_enum() -> None:
    """Annotated entries resolve via default, const, then the first enum value."""
    resolved, schema_definitions = split_configurable(
        {
            "mode": {"type": "string", "default": "draft"},
            "variant": {"type": "string", "const": "stable"},
            "choice": {"type": "string", "enum": ["alpha", "beta"]},
            "plain": "keep",
        }
    )

    assert resolved == {
        "mode": "draft",
        "variant": "stable",
        "choice": "alpha",
        "plain": "keep",
    }
    assert schema_definitions == {
        "mode": {"type": "string", "default": "draft"},
        "variant": {"type": "string", "const": "stable"},
        "choice": {"type": "string", "enum": ["alpha", "beta"]},
    }


def test_split_configurable_passes_plain_values_through() -> None:
    """A configurable mapping with no annotations yields no schema definitions."""
    resolved, schema_definitions = split_configurable({"plain": "value"})

    assert resolved == {"plain": "value"}
    assert schema_definitions == {}


def test_split_configurable_rejects_declaration_without_runtime_default() -> None:
    """A schema annotation that cannot resolve a value raises an error."""
    with pytest.raises(ConfigurableSchemaError, match="no runtime default"):
        split_configurable({"mode": {"type": "string", "enum": []}})
