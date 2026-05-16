"""Workflow upload tests for Python source handling."""

from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app


def test_workflow_upload_python_workflow_object_is_rejected(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """SDK Workflow object composition is rejected by Python-only ingest path."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text(
        "\n".join(
            [
                "class FakeWorkflow:",
                "    def to_deployment_payload(self):",
                "        return {'name': 'TestWorkflow'}",
                "",
                "workflow = FakeWorkflow()",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["workflow", "upload", str(py_file)], env=env)
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Only LangGraph Python scripts can be uploaded" in str(result.exception)


def test_workflow_upload_python_spec_loading_failure(
    runner: CliRunner,
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow upload handles spec_from_file_location failure."""
    py_file = tmp_path / "workflow.py"
    py_file.write_text("workflow = None", encoding="utf-8")

    def mock_spec_from_file_location(name: str, location: object) -> None:
        del name, location

    monkeypatch.setattr(
        importlib.util, "spec_from_file_location", mock_spec_from_file_location
    )

    result = runner.invoke(app, ["workflow", "upload", str(py_file)], env=env)
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Failed to load Python module" in str(result.exception)
