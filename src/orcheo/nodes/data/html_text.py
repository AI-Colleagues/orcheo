"""HTML text transformation node."""

from __future__ import annotations
import html
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


HtmlTextOperation = Literal["unescape", "escape", "normalize_nbsp"]


def _apply_html_text_operations(
    value: str,
    operations: Sequence[HtmlTextOperation],
) -> str:
    """Apply HTML text operations in order."""
    result = value
    for operation in operations:
        if operation == "unescape":
            result = html.unescape(result)
        elif operation == "escape":
            result = html.escape(result)
        elif operation == "normalize_nbsp":
            result = result.replace("\xa0", " ")
    return result


def _transform_all_strings(
    value: Any,
    operations: Sequence[HtmlTextOperation],
) -> Any:
    """Return a copy with every string transformed."""
    if isinstance(value, str):
        return _apply_html_text_operations(value, operations)
    if isinstance(value, Mapping):
        return {
            key: _transform_all_strings(nested, operations)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_transform_all_strings(item, operations) for item in value]
    return value


def _transform_path(
    value: Any,
    path_parts: Sequence[str],
    operations: Sequence[HtmlTextOperation],
) -> Any:
    """Return a copy with strings at ``path_parts`` transformed.

    Lists apply the same remaining path to each item, so a path like ``title``
    transforms every ``{"title": ...}`` object inside a list.
    """
    if not path_parts:
        return _transform_all_strings(value, operations)

    if isinstance(value, Mapping):
        key = path_parts[0]
        if key not in value:
            return dict(value)
        updated = dict(value)
        updated[key] = _transform_path(value[key], path_parts[1:], operations)
        return updated

    if isinstance(value, list):
        return [_transform_path(item, path_parts, operations) for item in value]

    return value


@registry.register(
    NodeMetadata(
        name="HtmlTextTransformNode",
        description="Decode, escape, and normalise HTML text in structured data.",
        category="data",
    )
)
class HtmlTextTransformNode(TaskNode):
    """Apply HTML text transformations to a value or selected dotted paths."""

    input_data: Any = Field(description="Input text or structured payload")
    operations: list[HtmlTextOperation] = Field(
        default_factory=lambda: ["unescape"],
        description="HTML text operations applied in order",
    )
    fields: list[str] | None = Field(
        default=None,
        description=(
            "Optional dotted fields to transform. When omitted, all strings in "
            "the input payload are transformed."
        ),
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return transformed text or structured data."""
        del state, config
        if self.fields is None:
            return {"result": _transform_all_strings(self.input_data, self.operations)}

        result = self.input_data
        for field in self.fields:
            parts = [part for part in field.split(".") if part]
            result = _transform_path(result, parts, self.operations)
        return {"result": result}


__all__ = [
    "HtmlTextOperation",
    "HtmlTextTransformNode",
    "_apply_html_text_operations",
]
