"""Helpers for loading and projecting ChatKit widgets as tools."""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from chatkit.widgets import WidgetRoot, WidgetTemplate
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, create_model


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WidgetDefinition:
    """Represent a loaded ChatKit widget definition."""

    name: str
    version: str
    json_schema: dict[str, Any]
    output_json_preview: dict[str, Any]
    template: str
    encoded_widget: str | None
    file_path: Path


def _sanitize_tool_name(widget_name: str) -> str:
    """Convert widget names into safe snake_case identifiers."""
    sanitized = widget_name.lower().replace(" ", "_").replace("-", "_")
    sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def _to_camel_case(snake_str: str) -> str:
    """Convert a snake_case string into CamelCase."""
    components = snake_str.split("_")
    return "".join(x.title() for x in components)


def _to_title_case(snake_or_lower: str) -> str:
    """Convert a string to TitleCase."""
    if "_" in snake_or_lower:
        components = snake_or_lower.split("_")
        return "".join(component.title() for component in components)
    return snake_or_lower.capitalize()


def _get_type_map() -> dict[str, type]:
    """Return mapping from JSON Schema types to Python types."""
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }


def _resolve_array_type(
    field_schema: dict[str, Any], model_name: str, field_name: str
) -> Any:
    """Resolve Python type for array field schema."""
    items_schema = field_schema.get("items")
    if not isinstance(items_schema, dict):
        return list[Any]

    item_type = items_schema.get("type")
    if item_type == "object":
        item_model_name = f"{model_name}{_to_title_case(field_name)}Item"
        item_model = json_schema_to_pydantic(items_schema, item_model_name)
        return list[item_model]  # type: ignore[valid-type]
    if item_type == "array":
        return list[Any]

    type_map = _get_type_map()
    mapped_type = type_map.get(item_type or "", Any)
    return list[mapped_type]  # type: ignore[valid-type]


def _resolve_field_type(
    field_schema: dict[str, Any], model_name: str, field_name: str
) -> Any:
    """Resolve Python type for a field based on its schema."""
    field_type = field_schema.get("type")

    if field_type == "object":
        nested_model_name = f"{model_name}{_to_title_case(field_name)}"
        return json_schema_to_pydantic(field_schema, nested_model_name)
    if field_type == "array":
        return _resolve_array_type(field_schema, model_name, field_name)

    type_map = _get_type_map()
    return type_map.get(field_type or "", Any)


def _build_field_definitions(
    properties: dict[str, Any], required_fields: set[str], model_name: str
) -> dict[str, Any]:
    """Build field definitions dict for Pydantic model creation."""
    field_definitions: dict[str, Any] = {}

    for field_name, field_schema in properties.items():
        python_type = _resolve_field_type(field_schema, model_name, field_name)

        if field_name not in required_fields:
            python_type = python_type | None
            field_definitions[field_name] = (python_type, None)
        else:
            field_definitions[field_name] = (python_type, ...)

    return field_definitions


def _build_model_config(
    schema: dict[str, Any], schema_title: str | None
) -> dict[str, Any]:
    """Build configuration kwargs for Pydantic model."""
    config_kwargs: dict[str, Any] = {}
    if schema_title:
        config_kwargs["title"] = schema_title
    if schema.get("additionalProperties") is False:
        config_kwargs["extra"] = "forbid"
    return config_kwargs


def json_schema_to_pydantic(
    schema: dict[str, Any],
    model_name: str = "DynamicModel",
    schema_title: str | None = None,
) -> type[BaseModel]:
    """Convert a JSON schema to a Pydantic model."""
    if schema.get("type") != "object":
        raise ValueError("Root schema must be of type 'object'")

    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    field_definitions = _build_field_definitions(
        properties, required_fields, model_name
    )
    config_kwargs = _build_model_config(schema, schema_title)

    if config_kwargs:
        config = ConfigDict(**config_kwargs)  # type: ignore[typeddict-item]
        return create_model(model_name, __config__=config, **field_definitions)

    return create_model(model_name, **field_definitions)


def _model_metadata(widget_name: str) -> tuple[str, str]:
    """Return consistent Pydantic metadata derived from a widget name."""
    camel_name = _to_camel_case(_sanitize_tool_name(widget_name))
    return f"{camel_name}Model", f"{camel_name}Arguments"


@cache
def _build_pydantic_model(schema_dump: str, widget_name: str) -> type[BaseModel]:
    """Return a cached Pydantic model class for the given schema snapshot."""
    schema = json.loads(schema_dump)
    model_name, schema_title = _model_metadata(widget_name)
    return json_schema_to_pydantic(schema, model_name, schema_title)


def build_widget_model(widget_def: WidgetDefinition) -> type[BaseModel]:
    """Convert a widget definition's JSON schema into a Pydantic model."""
    schema_dump = json.dumps(widget_def.json_schema, sort_keys=True)
    return _build_pydantic_model(schema_dump, widget_def.name)


@cache
def _load_widget_template(template_path: str) -> WidgetTemplate:
    """Load and cache a WidgetTemplate from disk."""
    return WidgetTemplate.from_file(template_path)


def render_widget_definition(widget_def: WidgetDefinition, **kwargs: Any) -> WidgetRoot:
    """Validate inputs, render the widget's template, and return a WidgetRoot."""
    model = build_widget_model(widget_def)
    validated = model(**kwargs)
    template = _load_widget_template(str(widget_def.file_path))
    render_context = validated.model_dump()
    render_context["undefined"] = None
    return template.build(render_context)


def _validate_widgets_dir(widgets_dir: Path | None) -> Path:
    """Ensure the provided widgets directory exists and is a directory."""
    if widgets_dir is None:
        raise ValueError("The widgets_dir argument is required to load widgets.")
    resolved_dir = widgets_dir.resolve()
    if not resolved_dir.exists():
        raise ValueError(f"Widgets directory does not exist: {resolved_dir}")
    if not resolved_dir.is_dir():
        raise ValueError(f"Widgets directory is not a directory: {resolved_dir}")
    return resolved_dir


def _collect_widget_files(base_dir: Path) -> list[Path]:
    """Return .widget files that physically live inside the base directory."""
    base_resolved = base_dir.resolve()
    widget_files: list[Path] = []
    seen_files: set[Path] = set()
    for widget_file in sorted(base_dir.rglob("*.widget")):
        if not widget_file.is_file():
            continue
        try:
            resolved_path = widget_file.resolve()
        except OSError as exc:
            logger.warning(
                "Skipping widget %s because it could not be resolved (%s)",
                widget_file,
                exc,
            )
            continue
        try:
            resolved_path.relative_to(base_resolved)
        except ValueError:
            logger.warning(
                "Skipping widget %s because it is outside the widgets directory",
                widget_file,
            )
            continue
        if resolved_path in seen_files:
            continue
        seen_files.add(resolved_path)
        widget_files.append(widget_file)
    return widget_files


def load_widget(widget_path: Path) -> WidgetDefinition:
    """Load a widget definition from a .widget file."""
    with widget_path.open(encoding="utf-8") as f:
        data = json.load(f)

    required_fields = ["name", "version", "jsonSchema", "outputJsonPreview", "template"]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        raise ValueError(
            f"Widget file {widget_path} missing required fields: "
            f"{', '.join(missing_fields)}"
        )

    template_value = data["template"]
    if not isinstance(template_value, str):
        raise ValueError(f"Widget template must be a string: {widget_path}")

    return WidgetDefinition(
        name=str(data["name"]),
        version=str(data["version"]),
        json_schema=data["jsonSchema"],
        output_json_preview=data["outputJsonPreview"],
        template=template_value,
        encoded_widget=data.get("encodedWidget"),
        file_path=widget_path,
    )


def load_widgets(widgets_dir: Path | str | None) -> list[WidgetDefinition]:
    """Load all .widget files from a curated directory."""
    if isinstance(widgets_dir, str):
        widgets_dir = Path(widgets_dir)
    base_dir = _validate_widgets_dir(widgets_dir)
    widget_files = _collect_widget_files(base_dir)

    if not widget_files:
        logger.warning("No widget definitions found in %s", base_dir)

    return [load_widget(widget_file) for widget_file in widget_files]


def _widget_tool_result(
    widget_def: WidgetDefinition,
    widget_root: WidgetRoot,
) -> tuple[str, dict[str, Any]]:
    """Return content and artifact payload for a rendered widget."""
    copy_text = f"Rendered {widget_def.name} widget."
    return copy_text, {
        "structured_content": widget_root.model_dump(exclude_none=True),
        "copy_text": copy_text,
    }


def _create_widget_tool_function(
    widget_def: WidgetDefinition,
) -> StructuredTool:
    """Create a LangChain tool for a given widget definition."""
    args_schema = build_widget_model(widget_def)

    def widget_tool(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        widget_root = render_widget_definition(widget_def, **kwargs)
        return _widget_tool_result(widget_def, widget_root)

    async def widget_tool_async(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        widget_root = render_widget_definition(widget_def, **kwargs)
        return _widget_tool_result(widget_def, widget_root)

    return StructuredTool.from_function(
        func=widget_tool,
        coroutine=widget_tool_async,
        name=_sanitize_tool_name(widget_def.name),
        description=(
            f"Generate a {widget_def.name} ChatKit widget from structured input."
        ),
        args_schema=args_schema,
        return_direct=True,
        response_format="content_and_artifact",
    )


def build_chatkit_widget_tools(
    widgets_dir: Path | str | None,
) -> list[BaseTool]:
    """Load widget definitions from disk and project them into tools."""
    if widgets_dir is None:
        raise ValueError("The widgets_dir argument is required to build widget tools.")

    resolved_dir = Path(widgets_dir).expanduser().resolve()
    widget_defs = load_widgets(resolved_dir)
    return [_create_widget_tool_function(widget_def) for widget_def in widget_defs]


__all__ = [
    "WidgetDefinition",
    "build_chatkit_widget_tools",
    "build_widget_model",
    "json_schema_to_pydantic",
    "load_widget",
    "load_widgets",
    "render_widget_definition",
]
