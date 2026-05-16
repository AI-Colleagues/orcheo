"""Workflow upload validation error tests."""

from __future__ import annotations
from pathlib import Path
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app


def test_workflow_upload_python_file_offline_error(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload fails in offline mode."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text("workflow = None", encoding="utf-8")
    result = runner.invoke(
        app,
        ["--offline", "workflow", "upload", str(py_file)],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "network connectivity" in str(result.exception)


def test_workflow_upload_file_not_exists(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Test workflow upload fails when file does not exist."""
    result = runner.invoke(
        app,
        ["workflow", "upload", "/nonexistent/file.py"],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "does not exist" in str(result.exception)


def test_workflow_upload_path_is_not_file(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload fails when path is not a file."""
    result = runner.invoke(
        app,
        ["workflow", "upload", str(tmp_path)],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "is not a file" in str(result.exception)


def test_workflow_upload_unsupported_file_type(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload fails with unsupported file extension."""
    txt_file = tmp_path / "workflow.txt"
    txt_file.write_text("some content", encoding="utf-8")
    result = runner.invoke(
        app,
        ["workflow", "upload", str(txt_file)],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Unsupported file type" in str(result.exception)


def test_workflow_upload_invalid_runnable_config(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test workflow upload rejects invalid JSON config payloads."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text("workflow = object()", encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "upload", str(py_file), "--config", "{not-json"],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Invalid JSON payload" in str(result.exception)
