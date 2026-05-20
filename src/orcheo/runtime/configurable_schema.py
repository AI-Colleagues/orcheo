"""Resolve inline JSON Schema annotations embedded in runnable config values.

A workflow's ``configurable`` mapping may declare a field as an inline JSON
Schema object (``{"type": ..., "enum": ..., "default": ...}``) so the Canvas
config form can render a typed widget. The workflow runtime, however, only
expects the resolved value. :func:`split_configurable` separates the two: it
returns the runtime values alongside the schema declarations so the latter can
be stored as version metadata instead of leaking into the runnable config.
"""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any


_SCHEMA_KEYS = frozenset(
    {
        "type",
        "enum",
        "items",
        "properties",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "default",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "additionalProperties",
    }
)
_SCHEMA_DISCRIMINATOR_KEYS = frozenset(
    {
        "enum",
        "items",
        "properties",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "default",
        "additionalProperties",
    }
)


class ConfigurableSchemaError(ValueError):
    """Raised when an inline schema annotation cannot resolve a runtime value."""


def is_schema_declaration(value: object) -> bool:
    """Return True when ``value`` is an explicit inline JSON Schema annotation."""
    if not isinstance(value, Mapping):
        return False
    if not any(key in value for key in _SCHEMA_KEYS):
        return False
    return any(key in value for key in _SCHEMA_DISCRIMINATOR_KEYS)


def _resolve_runtime_default(schema: Mapping[str, Any], *, key: str) -> Any:
    """Return the runtime value declared by an inline schema annotation."""
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    enum_value = schema.get("enum")
    if isinstance(enum_value, list) and enum_value:
        return enum_value[0]
    raise ConfigurableSchemaError(
        f"Configurable field '{key}' declares schema metadata but no runtime "
        "default. Add a 'default' value or a non-empty 'enum'."
    )


def split_configurable(
    configurable: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split inline schema annotations out of a ``configurable`` mapping.

    Returns a ``(resolved, schema_definitions)`` tuple where ``resolved`` carries
    the runtime values (each annotated entry replaced by its declared default)
    and ``schema_definitions`` maps every annotated key to its schema object.
    Plain (non-annotated) values pass through unchanged.
    """
    resolved: dict[str, Any] = {}
    schema_definitions: dict[str, Any] = {}
    for key, value in configurable.items():
        if is_schema_declaration(value):
            schema = dict(value)
            resolved[key] = _resolve_runtime_default(schema, key=key)
            schema_definitions[key] = schema
        else:
            resolved[key] = value
    return resolved, schema_definitions


__all__ = [
    "ConfigurableSchemaError",
    "is_schema_declaration",
    "split_configurable",
]
