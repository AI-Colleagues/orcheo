"""Tests for CLI edge commands."""

from __future__ import annotations
from typer.testing import CliRunner
from orcheo_sdk.cli.main import app


def test_edge_list_shows_registered_edges(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test edge list command displays registered edges."""
    result = runner.invoke(app, ["edge", "list"], env=env)
    assert result.exit_code == 0
    assert "Name" in result.stdout
    assert "Category" in result.stdout
    assert "Description" in result.stdout
    assert "IfElseEdge" in result.stdout
    assert "SwitchEdge" in result.stdout
    assert "WhileEdge" in result.stdout


def test_edge_list_with_category_filter(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge list with category filter."""
    result = runner.invoke(app, ["edge", "list", "--category", "control"], env=env)
    assert result.exit_code == 0
    # Should show filtered results
    assert "Available Edges" in result.stdout


def test_edge_show_displays_schema(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge show command displays edge metadata and schema."""
    result = runner.invoke(app, ["edge", "show", "IfElseEdge"], env=env)
    assert result.exit_code == 0
    assert "IfElseEdge" in result.stdout


def test_edge_show_nonexistent_error(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge show with non-existent edge returns error."""
    result = runner.invoke(app, ["edge", "show", "NonexistentEdge"], env=env)
    assert result.exit_code != 0


def test_edge_show_no_schema_info(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge show with edge that has neither schema nor attributes."""
    from orcheo.edges.registry import EdgeMetadata, edge_registry

    # Register a test edge without model_json_schema and without annotations
    test_meta = EdgeMetadata(
        name="TestEdgeNoInfo",
        description="Test edge without schema",
        category="test",
    )

    class TestEdgeNoInfo:
        """Edge without model_json_schema and no annotations."""

        pass

    # Register the test edge
    edge_registry._edges["TestEdgeNoInfo"] = TestEdgeNoInfo
    edge_registry._metadata["TestEdgeNoInfo"] = test_meta

    try:
        result = runner.invoke(app, ["edge", "show", "TestEdgeNoInfo"], env=env)
        assert result.exit_code == 0
        assert "TestEdgeNoInfo" in result.stdout
        assert "No schema information available" in result.stdout
    finally:
        # Clean up
        edge_registry._edges.pop("TestEdgeNoInfo", None)
        edge_registry._metadata.pop("TestEdgeNoInfo", None)


def test_edge_show_with_attributes_only(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge show with edge that has attributes but no model_json_schema."""
    from orcheo.edges.registry import EdgeMetadata, edge_registry

    # Register a test edge with annotations but no model_json_schema
    test_meta = EdgeMetadata(
        name="TestEdgeWithAttrs",
        description="Test edge with attributes",
        category="test",
    )

    class TestEdgeWithAttrs:
        """Edge with annotations but no model_json_schema."""

        test_attr: str
        count: int

    # Register the test edge
    edge_registry._edges["TestEdgeWithAttrs"] = TestEdgeWithAttrs
    edge_registry._metadata["TestEdgeWithAttrs"] = test_meta

    try:
        result = runner.invoke(app, ["edge", "show", "TestEdgeWithAttrs"], env=env)
        assert result.exit_code == 0
        assert "TestEdgeWithAttrs" in result.stdout
        assert "test_attr" in result.stdout
        assert "count" in result.stdout
    finally:
        # Clean up
        edge_registry._edges.pop("TestEdgeWithAttrs", None)
        edge_registry._metadata.pop("TestEdgeWithAttrs", None)


def test_edge_show_machine_mode(runner: CliRunner, machine_env: dict[str, str]) -> None:
    """Machine mode outputs JSON for edge show."""
    import json

    result = runner.invoke(app, ["edge", "show", "IfElseEdge"], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["name"] == "IfElseEdge"
    assert "aliases" not in data


def test_edge_show_with_schema(runner: CliRunner, env: dict[str, str]) -> None:
    """Test edge show with edge that has a Pydantic schema."""
    from pydantic import BaseModel
    from orcheo.edges.registry import EdgeMetadata, edge_registry

    # Register a test edge with model_json_schema
    test_meta = EdgeMetadata(
        name="TestEdgeWithSchema",
        description="Test edge with schema",
        category="test",
    )

    class TestEdgeWithSchema(BaseModel):
        """Edge with Pydantic model schema."""

        test_field: str
        number_field: int

    # Register the test edge
    edge_registry._edges["TestEdgeWithSchema"] = TestEdgeWithSchema
    edge_registry._metadata["TestEdgeWithSchema"] = test_meta

    try:
        result = runner.invoke(app, ["edge", "show", "TestEdgeWithSchema"], env=env)
        assert result.exit_code == 0
        assert "TestEdgeWithSchema" in result.stdout
        assert "Pydantic schema" in result.stdout
    finally:
        # Clean up
        edge_registry._edges.pop("TestEdgeWithSchema", None)
        edge_registry._metadata.pop("TestEdgeWithSchema", None)
