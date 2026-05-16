"""Workflow upload CLI tests for LangGraph error scenarios."""

from __future__ import annotations
from pathlib import Path
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app


def test_workflow_upload_langgraph_script_fetch_existing_error(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload fails when fetching existing workflow fails."""
    py_file = tmp_path / "langgraph_workflow.py"
    py_file.write_text(
        """
from langgraph.graph import StateGraph

def greet(state):
    return {"message": "Hello"}
""",
        encoding="utf-8",
    )

    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(404, json={"error": "Not found"})
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file), "--id", "wf-1"],
            env=env,
        )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Failed to fetch workflow" in str(result.exception)


def test_workflow_upload_langgraph_script_create_workflow_error(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload fails when creating new workflow fails."""
    py_file = tmp_path / "langgraph_workflow.py"
    py_file.write_text(
        """
from langgraph.graph import StateGraph

def greet(state):
    return {"message": "Hello"}
""",
        encoding="utf-8",
    )

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(500, json={"error": "Internal error"})
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Failed to create workflow" in str(result.exception)


def test_workflow_upload_with_blank_name_error(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload rejects empty rename values."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text(
        "workflow = object()",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["workflow", "upload", str(py_file), "--name", "   "],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "cannot be empty" in str(result.exception)
