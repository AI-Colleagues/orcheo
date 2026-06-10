"""Tests for native ChatKit widget helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode, build_chatkit_widget_tools
from orcheo.widgets.chatkit import (
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
