"""Tests for native ChatKit widget helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode, build_chatkit_widget_tools
from orcheo.widgets.chatkit import (
    _collect_widget_files,
    _create_widget_tool_function,
    _sanitize_tool_name,
    _to_title_case,
    _validate_widgets_dir,
    build_chatkit_widget_tools as _build_widget_tools_direct,
    json_schema_to_pydantic,
    load_widget,
    load_widgets,
    render_widget_definition,
)


def _write_widget_file(base_dir: Path, name: str = "Greeting Card") -> Path:
    widget_path = base_dir / f"{name}.widget"
    widget_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": name,
                "template": (
                    '{"type":"Card","children":[{"type":"Text","value":'
                    "{{ (title) | tojson }} }]}"
                ),
                "jsonSchema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                        }
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
                "outputJsonPreview": {
                    "type": "Card",
                    "children": [
                        {
                            "type": "Text",
                            "value": "Hello",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return widget_path


def test_json_schema_to_pydantic_rejects_non_object_root() -> None:
    """The schema converter should reject non-object roots."""

    with pytest.raises(ValueError, match="Root schema must be of type 'object'"):
        json_schema_to_pydantic({"type": "string"})


def test_load_widget_and_render_widget_definition(tmp_path: Path) -> None:
    """Widget definitions should load and render into ChatKit roots."""

    widget_path = _write_widget_file(tmp_path)
    widget_def = load_widget(widget_path)

    assert widget_def.name == "Greeting Card"
    assert widget_def.version == "1.0"

    widget_root = render_widget_definition(widget_def, title="Hello Orcheo")
    rendered = widget_root.model_dump(exclude_none=True)

    assert rendered["type"] == "Card"
    assert rendered["children"][0]["value"] == "Hello Orcheo"


def test_load_widgets_returns_empty_list_for_empty_directory(
    tmp_path: Path,
) -> None:
    """Empty curated directories should return an empty widget list."""

    assert load_widgets(tmp_path) == []


def test_load_widget_requires_required_fields(tmp_path: Path) -> None:
    """Widget files missing required keys should raise a validation error."""

    widget_path = tmp_path / "Broken.widget"
    widget_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": "Broken",
                "jsonSchema": {"type": "object", "properties": {}},
                "outputJsonPreview": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required fields"):
        load_widget(widget_path)


def test_agentnode_defaults_chatkit_widgets_dir() -> None:
    """AgentNode should default to the bundled ChatKit widgets directory."""

    agent = AgentNode(
        name="test_agent",
        ai_model="openai:gpt-4o-mini",
        system_prompt="Test prompt",
    )

    assert agent.chatkit_widgets_dir == "/app/examples/chatkit_widgets/widgets"


def test_build_chatkit_widget_tools_projects_widget_tools(
    tmp_path: Path,
) -> None:
    """Widget definitions should be projected into direct-return tools."""

    _write_widget_file(tmp_path)
    tools = build_chatkit_widget_tools(tmp_path)

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "greeting_card"
    assert tool.return_direct is True
    assert tool.response_format == "content_and_artifact"
    assert tool.invoke({"title": "Hello"}) == "Rendered Greeting Card widget."


@pytest.mark.asyncio
@patch("orcheo.nodes.ai.create_agent")
@patch("orcheo.nodes.ai.MultiServerMCPClient")
async def test_agent_prepares_chatkit_widget_tools(
    mock_mcp_client_class,
    mock_create_agent,
    tmp_path: Path,
) -> None:
    """AgentNode should merge native widget tools into the tool list."""

    _write_widget_file(tmp_path)
    mock_mcp_client = AsyncMock()
    mock_mcp_client.get_tools.return_value = []
    mock_mcp_client_class.return_value = mock_mcp_client
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {
        "messages": [{"role": "assistant", "content": "ok"}]
    }
    mock_create_agent.return_value = mock_agent

    agent = AgentNode(
        name="test_agent",
        ai_model="openai:gpt-4o-mini",
        system_prompt="Test prompt",
        predefined_tools=[],
        workflow_tools=[],
        use_chatkit_widget_tools=True,
        chatkit_widgets_dir=str(tmp_path),
    )

    state: State = {"messages": [{"role": "user", "content": "build a widget"}]}
    config = RunnableConfig()

    await agent.run(state, config)

    mock_create_agent.assert_called_once()
    call_kwargs = mock_create_agent.call_args[1]
    assert len(call_kwargs["tools"]) == 1
    assert call_kwargs["tools"][0].name == "greeting_card"


# ---------------------------------------------------------------------------
# _sanitize_tool_name – digit-leading name (line 36)
# ---------------------------------------------------------------------------


def test_sanitize_tool_name_prefixes_digit_start() -> None:
    """Widget names starting with a digit get a leading underscore."""
    assert _sanitize_tool_name("1Widget") == "_1widget"


# ---------------------------------------------------------------------------
# _to_title_case – both branches (lines 48-51)
# ---------------------------------------------------------------------------


def test_to_title_case_with_underscore() -> None:
    """Snake-case strings are converted to TitleCase (lines 48-50)."""
    assert _to_title_case("foo_bar") == "FooBar"


def test_to_title_case_without_underscore() -> None:
    """Plain lowercase strings are capitalised (line 51)."""
    assert _to_title_case("foo") == "Foo"


# ---------------------------------------------------------------------------
# json_schema_to_pydantic – nested object field (lines 94-95 + 48-50 via
# _to_title_case on a snake_case field name)
# ---------------------------------------------------------------------------


def test_json_schema_to_pydantic_nested_object_field() -> None:
    """Nested-object schema property triggers _resolve_field_type object branch."""
    schema = {
        "type": "object",
        "properties": {
            "home_address": {  # underscore → _to_title_case hits lines 48-50
                "type": "object",
                "properties": {"street": {"type": "string"}},
            }
        },
    }
    model = json_schema_to_pydantic(schema)
    assert issubclass(model, BaseModel)


# ---------------------------------------------------------------------------
# _resolve_array_type – all branches (lines 70-84, line 97)
# ---------------------------------------------------------------------------


def test_json_schema_to_pydantic_array_field_no_items() -> None:
    """Array property with no items schema resolves to list[Any] (lines 70-72)."""
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array"}},
    }
    model = json_schema_to_pydantic(schema)
    assert issubclass(model, BaseModel)


def test_json_schema_to_pydantic_array_of_objects() -> None:
    """Array of objects creates a nested Pydantic model (lines 75-78, line 51 via _to_title_case)."""
    schema = {
        "type": "object",
        "properties": {
            "rows": {  # no underscore → _to_title_case hits line 51
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            }
        },
    }
    model = json_schema_to_pydantic(schema)
    assert issubclass(model, BaseModel)


def test_json_schema_to_pydantic_array_of_arrays() -> None:
    """Array-of-arrays items type returns list[Any] (lines 79-80)."""
    schema = {
        "type": "object",
        "properties": {
            "matrix": {
                "type": "array",
                "items": {"type": "array"},
            }
        },
    }
    model = json_schema_to_pydantic(schema)
    assert issubclass(model, BaseModel)


def test_json_schema_to_pydantic_array_of_primitives() -> None:
    """Array of primitive items uses type_map fallback (lines 82-84, line 97)."""
    schema = {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
    }
    model = json_schema_to_pydantic(schema)
    assert issubclass(model, BaseModel)


# ---------------------------------------------------------------------------
# _build_field_definitions – optional field path (lines 113-114)
# ---------------------------------------------------------------------------


def test_json_schema_to_pydantic_optional_field() -> None:
    """Fields absent from required[] are typed as optional (lines 113-114)."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "nickname": {"type": "string"},
        },
        "required": ["name"],
    }
    model = json_schema_to_pydantic(schema)
    instance = model(name="Alice")  # type: ignore[call-arg]
    assert instance.nickname is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _build_model_config branches + bare create_model path (126->128, 128->130, 154)
# ---------------------------------------------------------------------------


def test_json_schema_to_pydantic_without_config() -> None:
    """Schema with no title and no additionalProperties uses bare create_model (line 154)."""
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    model = json_schema_to_pydantic(
        schema
    )  # schema_title=None, no additionalProperties
    assert issubclass(model, BaseModel)


# ---------------------------------------------------------------------------
# _validate_widgets_dir error paths (lines 196, 199, 201)
# ---------------------------------------------------------------------------


def test_validate_widgets_dir_raises_for_none() -> None:
    """None argument raises ValueError (line 196)."""
    with pytest.raises(ValueError, match="required to load widgets"):
        _validate_widgets_dir(None)


def test_validate_widgets_dir_raises_for_nonexistent(tmp_path: Path) -> None:
    """Non-existent path raises ValueError (line 199)."""
    with pytest.raises(ValueError, match="does not exist"):
        _validate_widgets_dir(tmp_path / "missing")


def test_validate_widgets_dir_raises_for_file(tmp_path: Path) -> None:
    """A regular file path (not a directory) raises ValueError (line 201)."""
    file_path = tmp_path / "notadir"
    file_path.touch()
    with pytest.raises(ValueError, match="not a directory"):
        _validate_widgets_dir(file_path)


# ---------------------------------------------------------------------------
# _collect_widget_files edge cases (lines 212, 215-221, 224-229, 231)
# ---------------------------------------------------------------------------


def test_collect_widget_files_skips_non_file_entries(tmp_path: Path) -> None:
    """A directory named *.widget is skipped (line 212)."""
    (tmp_path / "fake.widget").mkdir()
    assert _collect_widget_files(tmp_path) == []


def test_collect_widget_files_skips_unresolvable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """OSError during resolve is caught and logged (lines 215-221)."""
    (tmp_path / "broken.widget").write_text("{}")

    original_resolve = Path.resolve

    def conditional_resolve(self: Path, strict: bool = False) -> Path:
        if str(self).endswith(".widget"):
            raise OSError("cannot resolve")
        return original_resolve(self, strict)

    with patch.object(Path, "resolve", conditional_resolve):
        with caplog.at_level(logging.WARNING, logger="orcheo.widgets.chatkit"):
            result = _collect_widget_files(tmp_path)

    assert result == []
    assert "could not be resolved" in caplog.text


def test_collect_widget_files_skips_outside_dir(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Symlinks pointing outside the base directory are skipped (lines 224-229)."""
    outer = tmp_path / "outer"
    outer.mkdir()
    outside_widget = outer / "outside.widget"
    outside_widget.write_text(json.dumps({"name": "x"}))

    base_dir = tmp_path / "widgets"
    base_dir.mkdir()
    (base_dir / "link.widget").symlink_to(outside_widget)

    with caplog.at_level(logging.WARNING, logger="orcheo.widgets.chatkit"):
        result = _collect_widget_files(base_dir)

    assert result == []
    assert "outside the widgets directory" in caplog.text


def test_collect_widget_files_deduplicates_symlinks(tmp_path: Path) -> None:
    """Two paths resolving to the same file are counted once (line 231)."""
    real_widget = tmp_path / "real.widget"
    real_widget.write_text(json.dumps({"name": "x"}))
    (tmp_path / "link.widget").symlink_to(real_widget)

    result = _collect_widget_files(tmp_path)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# load_widget – non-string template (line 252)
# ---------------------------------------------------------------------------


def test_load_widget_raises_for_non_string_template(tmp_path: Path) -> None:
    """Widget file whose template is not a string raises ValueError (line 252)."""
    widget_path = tmp_path / "bad.widget"
    widget_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": "bad",
                "template": {"not": "a string"},
                "jsonSchema": {"type": "object", "properties": {}},
                "outputJsonPreview": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="template must be a string"):
        load_widget(widget_path)


# ---------------------------------------------------------------------------
# load_widgets – string path input (line 268)
# ---------------------------------------------------------------------------


def test_load_widgets_accepts_string_path(tmp_path: Path) -> None:
    """load_widgets converts a str argument to Path before loading (line 268)."""
    _write_widget_file(tmp_path)
    widgets = load_widgets(str(tmp_path))
    assert len(widgets) == 1


# ---------------------------------------------------------------------------
# Async widget tool coroutine (lines 301-302)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_tool_async_invocation(tmp_path: Path) -> None:
    """ainvoke() exercises the async coroutine path in the widget tool (lines 301-302)."""
    widget_def = load_widget(_write_widget_file(tmp_path))
    tool = _create_widget_tool_function(widget_def)

    result = await tool.ainvoke({"title": "Async Hello"})
    assert result == "Rendered Greeting Card widget."


# ---------------------------------------------------------------------------
# build_chatkit_widget_tools – None guard (line 322)
# ---------------------------------------------------------------------------


def test_build_chatkit_widget_tools_raises_for_none() -> None:
    """None widgets_dir raises ValueError in build_chatkit_widget_tools (line 322)."""
    with pytest.raises(ValueError, match="required to build widget tools"):
        _build_widget_tools_direct(None)
