"""Tests for workflow Python file frontmatter parsing and integration."""

from __future__ import annotations
import json
from pathlib import Path
import httpx
import pytest
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.workflow.frontmatter import (
    WorkflowFrontmatter,
    load_workflow_frontmatter,
    parse_workflow_frontmatter,
    resolve_frontmatter_config,
    _detect_file_encoding,
)


def test_parse_returns_empty_when_no_block() -> None:
    source = "# regular comment\nprint('hello')\n"
    fm = parse_workflow_frontmatter(source)
    assert fm == WorkflowFrontmatter()
    assert fm.is_empty


def test_parse_extracts_all_fields() -> None:
    source = (
        "# /// orcheo\n"
        '# name = "My Workflow"\n'
        '# id = "wf-abc123"\n'
        '# config = "./wf.config.json"\n'
        '# entrypoint = "build_graph"\n'
        "# ///\n"
        "print('hello')\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "My Workflow"
    assert fm.workflow_id == "wf-abc123"
    assert fm.config_path == "./wf.config.json"
    assert fm.entrypoint == "build_graph"
    assert not fm.is_empty


def test_parse_accepts_handle_alias() -> None:
    source = '# /// orcheo\n# handle = "wf-handle"\n# ///\n'
    fm = parse_workflow_frontmatter(source)
    assert fm.workflow_id == "wf-handle"


def test_parse_rejects_id_and_handle_together() -> None:
    source = '# /// orcheo\n# id = "x"\n# handle = "y"\n# ///\n'
    with pytest.raises(CLIError, match="must not specify both 'id' and 'handle'"):
        parse_workflow_frontmatter(source)


def test_parse_rejects_unknown_field() -> None:
    source = '# /// orcheo\n# bogus = "x"\n# ///\n'
    with pytest.raises(CLIError, match="Unknown 'orcheo' frontmatter field"):
        parse_workflow_frontmatter(source)


def test_parse_rejects_non_string_field() -> None:
    source = "# /// orcheo\n# name = 123\n# ///\n"
    with pytest.raises(CLIError, match="must be a string"):
        parse_workflow_frontmatter(source)


def test_parse_rejects_empty_string_field() -> None:
    source = '# /// orcheo\n# name = "   "\n# ///\n'
    with pytest.raises(CLIError, match="must not be empty"):
        parse_workflow_frontmatter(source)


def test_parse_rejects_invalid_toml() -> None:
    source = '# /// orcheo\n# name = "unterminated\n# ///\n'
    with pytest.raises(CLIError, match="Invalid TOML"):
        parse_workflow_frontmatter(source)


def test_parse_rejects_multiple_blocks() -> None:
    source = (
        "# /// orcheo\n"
        '# name = "first"\n'
        "# ///\n"
        "\n"
        "# /// orcheo\n"
        '# name = "second"\n'
        "# ///\n"
    )
    with pytest.raises(CLIError, match="Multiple 'orcheo' frontmatter blocks"):
        parse_workflow_frontmatter(source)


def test_parse_ignores_other_block_types() -> None:
    """A non-orcheo PEP 723 block (e.g., 'script') is left alone."""
    source = (
        "# /// script\n"
        '# requires-python = ">=3.12"\n'
        "# ///\n"
        "\n"
        "# /// orcheo\n"
        '# name = "Real Workflow"\n'
        "# ///\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "Real Workflow"


def test_parse_handles_crlf_line_endings() -> None:
    """Frontmatter blocks work with Windows CRLF line endings."""
    source = (
        "# /// orcheo\r\n"
        '# name = "CRLF Workflow"\r\n'
        '# id = "wf-crlf"\r\n'
        "# ///\r\n"
        "print('hello')\r\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "CRLF Workflow"
    assert fm.workflow_id == "wf-crlf"


def test_parse_handles_mixed_line_endings() -> None:
    """Frontmatter blocks work with mixed LF/CRLF line endings."""
    source = (
        "# /// orcheo\n"
        '# name = "Mixed Endings"\r\n'
        '# id = "wf-mixed"\n'
        "# ///\r\n"
        "print('hello')\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "Mixed Endings"
    assert fm.workflow_id == "wf-mixed"


def test_load_from_file_reads_source(tmp_path: Path) -> None:
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        '# /// orcheo\n# name = "From File"\n# ///\n',
        encoding="utf-8",
    )
    fm = load_workflow_frontmatter(py_file)
    assert fm.name == "From File"


def test_load_from_file_with_pep263_encoding(tmp_path: Path) -> None:
    """Load workflow frontmatter from file with PEP 263 encoding declaration."""
    py_file = tmp_path / "wf_latin1.py"
    content = (
        "# -*- coding: latin-1 -*-\n"
        "# /// orcheo\n"
        '# name = "Encoded Workflow"\n'
        "# ///\n"
        "# This file uses latin-1 encoding\n"
        "print('café')\n"  # This will be encoded as latin-1
    )
    py_file.write_text(content, encoding="latin-1")
    
    fm = load_workflow_frontmatter(py_file)
    assert fm.name == "Encoded Workflow"


def test_load_from_file_with_encoding_on_second_line(tmp_path: Path) -> None:
    """PEP 263 allows encoding declaration on the second line."""
    py_file = tmp_path / "wf_second_line.py"
    content = (
        "#!/usr/bin/env python3\n"
        "# coding=utf-16\n"
        "# /// orcheo\n"
        '# name = "Second Line Encoding"\n'
        "# ///\n"
        "print('hello')\n"
    )
    py_file.write_text(content, encoding="utf-16")
    
    fm = load_workflow_frontmatter(py_file)
    assert fm.name == "Second Line Encoding"


def test_load_from_file_defaults_to_utf8(tmp_path: Path) -> None:
    """Files without encoding declaration default to UTF-8."""
    py_file = tmp_path / "wf_no_encoding.py"
    content = (
        "# /// orcheo\n"
        '# name = "No Encoding Declared"\n'
        "# ///\n"
        "print('hello')\n"
    )
    py_file.write_text(content, encoding="utf-8")
    
    fm = load_workflow_frontmatter(py_file)
    assert fm.name == "No Encoding Declared"


def test_resolve_config_relative_to_workflow(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    config = tmp_path / "wf.config.json"
    config.write_text(json.dumps({"tags": ["alpha"]}), encoding="utf-8")

    data = resolve_frontmatter_config(workflow, "wf.config.json")
    assert data == {"tags": ["alpha"]}


def test_resolve_config_missing_file_raises(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")

    with pytest.raises(CLIError, match="does not exist"):
        resolve_frontmatter_config(workflow, "missing.config.json")


def test_resolve_config_rejects_directory(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    (tmp_path / "configdir").mkdir()

    with pytest.raises(CLIError, match="is not a file"):
        resolve_frontmatter_config(workflow, "configdir")


def test_resolve_config_rejects_invalid_json(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")

    with pytest.raises(CLIError, match="Invalid JSON"):
        resolve_frontmatter_config(workflow, "bad.json")


def test_resolve_config_rejects_non_object(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(CLIError, match="must contain a JSON object"):
        resolve_frontmatter_config(workflow, "arr.json")


def _langgraph_script_with_frontmatter(
    *,
    workflow_id: str | None = None,
    name: str | None = None,
    config_path: str | None = None,
    entrypoint: str | None = None,
) -> str:
    lines = ["# /// orcheo"]
    if name is not None:
        lines.append(f'# name = "{name}"')
    if workflow_id is not None:
        lines.append(f'# id = "{workflow_id}"')
    if config_path is not None:
        lines.append(f'# config = "{config_path}"')
    if entrypoint is not None:
        lines.append(f'# entrypoint = "{entrypoint}"')
    lines.append("# ///")
    lines.append("")
    lines.append("from langgraph.graph import StateGraph")
    lines.append("")
    lines.append("def build_graph():")
    lines.append("    return StateGraph(dict)")
    return "\n".join(lines) + "\n"


def test_upload_uses_frontmatter_id_for_existing_workflow(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Frontmatter id triggers the update path without needing --id."""
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        _langgraph_script_with_frontmatter(workflow_id="wf-known"),
        encoding="utf-8",
    )

    existing_workflow = {"id": "wf-known", "name": "existing"}
    created_version = {"id": "v-2", "version": 2, "workflow_id": "wf-known"}
    with respx.mock(assert_all_called=True) as router:
        router.get("http://api.test/api/workflows/wf-known").mock(
            return_value=httpx.Response(200, json=existing_workflow)
        )
        router.post("http://api.test/api/workflows/wf-known/versions/ingest").mock(
            return_value=httpx.Response(201, json=created_version)
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )

    assert result.exit_code == 0, result.stdout
    assert "Ingested LangGraph script as version 2" in result.stdout


def test_upload_uses_frontmatter_name_when_creating(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Frontmatter name overrides the filename-derived default."""
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        _langgraph_script_with_frontmatter(name="Frontmatter Workflow"),
        encoding="utf-8",
    )

    created_workflow = {"id": "wf-new", "name": "Frontmatter Workflow"}
    created_version = {"id": "v-1", "version": 1, "workflow_id": "wf-new"}
    with respx.mock(assert_all_called=True) as router:
        create_route = router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(201, json=created_workflow)
        )
        router.post("http://api.test/api/workflows/wf-new/versions/ingest").mock(
            return_value=httpx.Response(201, json=created_version)
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )

    assert result.exit_code == 0, result.stdout
    body = json.loads(create_route.calls[0].request.content)
    assert body["name"] == "Frontmatter Workflow"
    assert body["slug"] == "frontmatter-workflow"


def test_upload_loads_companion_config_from_frontmatter(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Frontmatter config path loads the companion JSON file."""
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        _langgraph_script_with_frontmatter(config_path="wf.config.json"),
        encoding="utf-8",
    )
    config_file = tmp_path / "wf.config.json"
    config_file.write_text('{"tags": ["from-frontmatter"]}', encoding="utf-8")

    created_workflow = {"id": "wf-new", "name": "wf"}
    created_version = {"id": "v-1", "version": 1, "workflow_id": "wf-new"}
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(201, json=created_workflow)
        )
        ingest_route = router.post(
            "http://api.test/api/workflows/wf-new/versions/ingest"
        ).mock(return_value=httpx.Response(201, json=created_version))
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )

    assert result.exit_code == 0, result.stdout
    request_body = json.loads(ingest_route.calls[0].request.content)
    assert request_body["runnable_config"] == {"tags": ["from-frontmatter"]}


def test_upload_cli_flag_overrides_frontmatter(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """An explicit --name on the CLI wins over the frontmatter name."""
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        _langgraph_script_with_frontmatter(name="From Frontmatter"),
        encoding="utf-8",
    )

    created_workflow = {"id": "wf-new", "name": "CLI Wins"}
    created_version = {"id": "v-1", "version": 1, "workflow_id": "wf-new"}
    with respx.mock(assert_all_called=True) as router:
        create_route = router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(201, json=created_workflow)
        )
        router.post("http://api.test/api/workflows/wf-new/versions/ingest").mock(
            return_value=httpx.Response(201, json=created_version)
        )
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file), "--name", "CLI Wins"],
            env=env,
        )

    assert result.exit_code == 0, result.stdout
    body = json.loads(create_route.calls[0].request.content)
    assert body["name"] == "CLI Wins"


def test_upload_uses_frontmatter_entrypoint(
    runner: CliRunner, env: dict[str, str], tmp_path: Path
) -> None:
    """Frontmatter entrypoint is forwarded to ingest."""
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        _langgraph_script_with_frontmatter(entrypoint="build_graph"),
        encoding="utf-8",
    )

    created_workflow = {"id": "wf-new", "name": "wf"}
    created_version = {"id": "v-1", "version": 1, "workflow_id": "wf-new"}
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows").mock(
            return_value=httpx.Response(201, json=created_workflow)
        )
        ingest_route = router.post(
            "http://api.test/api/workflows/wf-new/versions/ingest"
        ).mock(return_value=httpx.Response(201, json=created_version))
        result = runner.invoke(
            app,
            ["workflow", "upload", str(py_file)],
            env=env,
        )

    assert result.exit_code == 0, result.stdout
    request_body = json.loads(ingest_route.calls[0].request.content)
    assert request_body["entrypoint"] == "build_graph"


def test_parse_handles_empty_comment_lines() -> None:
    """Frontmatter parsing skips empty lines and malformed comment lines."""
    source = (
        "# /// orcheo\n"
        "#\n"  # Empty comment line
        '# name = "Robust Parsing"\n'
        "\n"   # Empty line (shouldn't happen but should be handled)
        "# ///\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "Robust Parsing"


def test_parse_handles_comment_only_lines() -> None:
    """Frontmatter parsing handles lines with only '#' character."""
    source = (
        "# /// orcheo\n"
        "#\n" 
        '# name = "Comment Only Test"\n'
        "#\n"
        "# ///\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "Comment Only Test"


def test_parse_handles_tabs_in_comment() -> None:
    """Frontmatter parsing handles tabs after # character."""
    source = (
        "# /// orcheo\n"
        "#\tname = \"Tab Test\"\n"  # Tab instead of space
        "# ///\n"
    )
    fm = parse_workflow_frontmatter(source)
    assert fm.name == "Tab Test"


def test_load_from_file_with_file_not_found_error(tmp_path: Path) -> None:
    """load_workflow_frontmatter raises CLIError for missing files."""
    missing_file = tmp_path / "does_not_exist.py"
    with pytest.raises(CLIError, match="Failed to read workflow file"):
        load_workflow_frontmatter(missing_file)


def test_load_from_file_with_permission_error(tmp_path: Path) -> None:
    """load_workflow_frontmatter handles permission errors gracefully."""
    import os
    import stat
    
    py_file = tmp_path / "restricted.py"
    py_file.write_text("# /// orcheo\n# name = \"test\"\n# ///\n")
    
    # Remove read permissions (if on Unix-like system)
    if hasattr(os, 'chmod'):
        try:
            os.chmod(py_file, stat.S_IWRITE)  # Write only
            with pytest.raises(CLIError, match="Failed to read workflow file"):
                load_workflow_frontmatter(py_file)
        finally:
            # Restore permissions for cleanup
            os.chmod(py_file, stat.S_IREAD | stat.S_IWRITE)


def test_load_from_file_with_invalid_encoding(tmp_path: Path) -> None:
    """load_workflow_frontmatter handles encoding errors gracefully."""
    py_file = tmp_path / "bad_encoding.py"
    
    # Write a file with declared latin-1 encoding but invalid UTF-8 bytes
    with py_file.open("wb") as f:
        f.write(b"# -*- coding: utf-8 -*-\n")
        f.write(b"# /// orcheo\n")
        f.write(b"# name = \"\xff\xfe\"  # Invalid UTF-8 bytes\n")  
        f.write(b"# ///\n")
    
    with pytest.raises(CLIError, match="Failed to decode workflow file"):
        load_workflow_frontmatter(py_file)


def test_detect_file_encoding_with_no_file(tmp_path: Path) -> None:
    """_detect_file_encoding returns utf-8 for non-existent files."""
    missing_file = tmp_path / "missing.py"
    assert _detect_file_encoding(missing_file) == "utf-8"


def test_detect_file_encoding_with_invalid_first_line(tmp_path: Path) -> None:
    """_detect_file_encoding handles files that can't be decoded as ASCII."""
    
    py_file = tmp_path / "binary_start.py"
    with py_file.open("wb") as f:
        f.write(b"\xff\xfe\x00\x00")  # Invalid ASCII/UTF-8 bytes
        f.write(b"\n# coding: latin-1\n")
    
    # Should default to utf-8 when first line can't be decoded
    assert _detect_file_encoding(py_file) == "utf-8"


def test_resolve_config_with_unicode_error_in_json(tmp_path: Path) -> None:
    """resolve_frontmatter_config handles JSON files that can't be decoded."""
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    
    config_file = tmp_path / "bad_unicode.json"
    with config_file.open("wb") as f:
        f.write(b'{"name": "\xff\xfe"}')  # Invalid UTF-8 in JSON
    
    with pytest.raises(CLIError, match="Invalid JSON"):
        resolve_frontmatter_config(workflow, "bad_unicode.json")
