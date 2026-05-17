"""Agent tool CLI command tests."""

from __future__ import annotations
from typer.testing import CliRunner
from orcheo_sdk.cli.main import app


def test_agent_tool_list_shows_all_tools(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool list command shows all registered tools."""
    result = runner.invoke(app, ["agent-tool", "list"], env=env)
    assert result.exit_code == 0
    # Should show some tools registered in the registry
    assert "Available Agent Tools" in result.stdout


def test_agent_tool_list_with_category_filter(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool list command with category filter."""
    result = runner.invoke(
        app, ["agent-tool", "list", "--category", "general"], env=env
    )
    assert result.exit_code == 0
    assert "Available Agent Tools" in result.stdout


def test_agent_tool_list_with_name_filter(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool list command filters by name when category matches."""
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    # Register a test tool
    test_meta = ToolMetadata(
        name="test_search_tool", description="Test search", category="search"
    )

    @tool_registry.register(test_meta)
    def test_tool() -> str:
        return "test"

    result = runner.invoke(app, ["agent-tool", "list", "--category", "search"], env=env)
    assert result.exit_code == 0
    assert "test_search_tool" in result.stdout or "search" in result.stdout.lower()


def test_agent_tool_show_displays_metadata(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool show command displays tool metadata."""
    from pydantic import BaseModel, Field
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    class TestSchema(BaseModel):
        query: str = Field(description="The search query")
        limit: int = Field(default=10, description="Result limit")

    # Register a test tool with schema
    test_meta = ToolMetadata(
        name="test_show_tool", description="Test tool for show", category="test"
    )

    @tool_registry.register(test_meta)
    class TestToolWithSchema:
        args_schema = TestSchema

    result = runner.invoke(app, ["agent-tool", "show", "test_show_tool"], env=env)
    assert result.exit_code == 0
    assert "test_show_tool" in result.stdout
    assert "Test tool for show" in result.stdout


def test_agent_tool_show_tool_not_found(runner: CliRunner, env: dict[str, str]) -> None:
    """Test agent-tool show command with non-existent tool."""
    result = runner.invoke(app, ["agent-tool", "show", "nonexistent_tool_xyz"], env=env)
    assert result.exit_code != 0
    assert "not registered" in result.stdout or "not registered" in str(
        result.exception
    )


def test_agent_tool_show_with_pydantic_model(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool show with direct Pydantic model."""
    from pydantic import BaseModel, Field
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    class DirectModel(BaseModel):
        """A direct Pydantic model."""

        field: str = Field(default="test", description="A test field")

    test_meta = ToolMetadata(
        name="test_pydantic_model", description="Direct Pydantic", category="test"
    )

    @tool_registry.register(test_meta)
    class ToolWithModel:
        @staticmethod
        def model_json_schema() -> dict:
            return DirectModel.model_json_schema()

    result = runner.invoke(app, ["agent-tool", "show", "test_pydantic_model"], env=env)
    assert result.exit_code == 0
    assert "test_pydantic_model" in result.stdout


def test_agent_tool_show_with_annotations(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool show with function annotations."""
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    test_meta = ToolMetadata(
        name="test_annotations_tool",
        description="Function with annotations",
        category="test",
    )

    @tool_registry.register(test_meta)
    def annotated_function(query: str, count: int) -> str:
        """A function with type annotations."""
        return f"{query} {count}"

    result = runner.invoke(
        app, ["agent-tool", "show", "test_annotations_tool"], env=env
    )
    assert result.exit_code == 0
    assert "test_annotations_tool" in result.stdout


def test_agent_tool_show_no_schema(runner: CliRunner, env: dict[str, str]) -> None:
    """Test agent-tool show with tool that has no schema."""
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    test_meta = ToolMetadata(
        name="test_no_schema_tool", description="Tool without schema", category="test"
    )

    @tool_registry.register(test_meta)
    class ToolWithoutSchema:
        pass

    result = runner.invoke(app, ["agent-tool", "show", "test_no_schema_tool"], env=env)
    assert result.exit_code == 0
    assert "test_no_schema_tool" in result.stdout
    assert (
        "No schema information available" in result.stdout
        or "Tool without schema" in result.stdout
    )


def test_agent_tool_show_args_schema_no_model_json_schema(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool show with args_schema but no model_json_schema."""
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    test_meta = ToolMetadata(
        name="test_args_no_model",
        description="Tool with args_schema but no model_json_schema",
        category="test",
    )

    class SchemaWithoutMethod:
        pass

    @tool_registry.register(test_meta)
    class ToolWithArgsNoSchema:
        args_schema = SchemaWithoutMethod()

    result = runner.invoke(app, ["agent-tool", "show", "test_args_no_model"], env=env)
    assert result.exit_code == 0
    assert "test_args_no_model" in result.stdout


def test_agent_tool_show_empty_annotations(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test agent-tool show with empty annotations."""
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    test_meta = ToolMetadata(
        name="test_empty_annotations",
        description="Function with empty annotations",
        category="test",
    )

    @tool_registry.register(test_meta)
    def function_no_annotations():
        """Function without annotations."""
        return "test"

    result = runner.invoke(
        app, ["agent-tool", "show", "test_empty_annotations"], env=env
    )
    assert result.exit_code == 0
    assert "test_empty_annotations" in result.stdout
    # Should show "No schema information available" since there are no annotations
    assert "No schema information available" in result.stdout


def test_agent_tool_list_machine_output(runner: CliRunner, env: dict[str, str]) -> None:
    """Test agent-tool list in machine mode outputs a markdown table."""
    machine_env = {k: v for k, v in env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(app, ["agent-tool", "list"], env=machine_env)
    assert result.exit_code == 0
    # Machine mode uses print_markdown_table which outputs pipe-delimited rows
    # or "(empty)" if no tools match
    assert "|" in result.stdout or "(empty)" in result.stdout


def test_agent_tool_show_machine_output(runner: CliRunner, env: dict[str, str]) -> None:
    """Test agent-tool show in machine mode outputs JSON."""
    import json
    from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry

    test_meta = ToolMetadata(
        name="test_machine_show_tool",
        description="Tool for machine output test",
        category="test",
    )

    @tool_registry.register(test_meta)
    def machine_tool() -> str:
        return "test"

    machine_env = {k: v for k, v in env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(
        app, ["agent-tool", "show", "test_machine_show_tool"], env=machine_env
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["name"] == "test_machine_show_tool"
