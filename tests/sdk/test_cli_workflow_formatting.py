"""Workflow upload validation and formatting tests."""

from __future__ import annotations
import sys
from pathlib import Path
import httpx
import pytest
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.workflow import _load_workflow_from_python


def test_load_workflow_from_python_missing_workflow_variable(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test loading Python file without 'workflow' treats it as LangGraph script."""
    py_file = tmp_path / "no_workflow.py"
    py_file.write_text("some_other_var = 123", encoding="utf-8")

    # Now it treats files without 'workflow' as LangGraph scripts
    # and tries to create a workflow and ingest them
    created_workflow = {"id": "wf-new", "name": "no-workflow"}
    # The ingestion will fail because it's not valid LangGraph code
    with respx.mock() as router:
        router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(201, json=created_workflow)
        )
        router.post("http://api.test/api/workflows/wf-new/versions/ingest").mock(
            return_value=httpx.Response(
                400, json={"detail": "Script did not produce a LangGraph StateGraph"}
            )
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Failed to ingest LangGraph script" in str(result.exception)


def test_load_workflow_from_python_wrong_type(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Test loading Python file with wrong workflow type fails."""
    py_file = tmp_path / "wrong_type.py"
    py_file.write_text("workflow = 'not a workflow object'", encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "upload", str(py_file)],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "must provide a to_deployment_payload() method" in str(result.exception)


def test_load_workflow_from_python_adds_managed_plugin_site_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Managed plugin installs should be importable during local workflow loading."""
    plugin_dir = tmp_path / "plugins"
    site_packages = (
        plugin_dir
        / "venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    module_dir = site_packages / "orcheo_plugin_fixture_runtime"
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text("VALUE = 'ready'\n", encoding="utf-8")

    workflow_file = tmp_path / "workflow.py"
    workflow_file.write_text(
        "\n".join(
            [
                "from orcheo_plugin_fixture_runtime import VALUE",
                "",
                "class FakeWorkflow:",
                "    def to_deployment_payload(self):",
                "        return {'_type': 'langgraph_script', 'script': VALUE}",
                "",
                "workflow = FakeWorkflow()",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(plugin_dir))

    before = list(sys.path)
    try:
        payload = _load_workflow_from_python(workflow_file)
    finally:
        sys.path[:] = before
        sys.modules.pop("orcheo_plugin_fixture_runtime", None)

    assert payload == {"_type": "langgraph_script", "script": "ready"}


def test_load_workflow_from_json_is_rejected(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """JSON upload files are rejected before parsing."""
    json_file = tmp_path / "array.json"
    json_file.write_text('["not", "an", "object"]', encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "upload", str(json_file)],
        env=env,
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "Only .py files are supported" in str(result.exception)


def test_format_workflow_as_python_from_langgraph_script() -> None:
    """Script versions are exported as their original source."""
    from orcheo_sdk.cli.workflow import _format_workflow_as_python

    workflow = {"name": "TestWorkflow"}
    graph = {"format": "langgraph-script", "source": "print('hello')\n"}

    result = _format_workflow_as_python(workflow, graph)
    assert result == "print('hello')\n"


def test_format_workflow_as_python_rejects_non_script_payload() -> None:
    """Non-script versions raise a clear export error."""
    from orcheo_sdk.cli.workflow import _format_workflow_as_python

    with pytest.raises(CLIError, match="unsupported format"):
        _format_workflow_as_python({"name": "legacy"}, {"nodes": [], "edges": []})


def test_format_workflow_as_python_rejects_blank_script_source() -> None:
    """Blank script source should be rejected even for langgraph-script format."""
    from orcheo_sdk.cli.workflow import _format_workflow_as_python

    with pytest.raises(CLIError, match="unsupported format"):
        _format_workflow_as_python(
            {"name": "empty-source"},
            {"format": "langgraph-script", "source": "   "},
        )
