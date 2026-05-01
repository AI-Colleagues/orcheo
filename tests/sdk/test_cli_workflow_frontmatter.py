"""Unit tests for workflow frontmatter helpers."""

from __future__ import annotations
import codecs
import importlib.util
import json
import sys
import types
from pathlib import Path
import pytest


def _load_frontmatter_module() -> types.ModuleType:
    """Load the frontmatter module without importing the full SDK package."""
    root = Path(__file__).resolve().parents[2]
    module_path = (
        root
        / "packages"
        / "sdk"
        / "src"
        / "orcheo_sdk"
        / "cli"
        / "workflow"
        / "frontmatter.py"
    )

    saved_modules = {
        name: sys.modules[name]
        for name in (
            "orcheo_sdk",
            "orcheo_sdk.cli",
            "orcheo_sdk.cli.errors",
            "orcheo_sdk.cli.workflow.frontmatter",
        )
        if name in sys.modules
    }

    orcheo_sdk_module = types.ModuleType("orcheo_sdk")
    orcheo_sdk_module.__path__ = []  # type: ignore[attr-defined]
    cli_module = types.ModuleType("orcheo_sdk.cli")
    cli_module.__path__ = []  # type: ignore[attr-defined]
    errors_module = types.ModuleType("orcheo_sdk.cli.errors")

    class CLIError(RuntimeError):
        """Minimal stand-in for the SDK CLI error type."""

    errors_module.CLIError = CLIError

    sys.modules["orcheo_sdk"] = orcheo_sdk_module
    sys.modules["orcheo_sdk.cli"] = cli_module
    sys.modules["orcheo_sdk.cli.errors"] = errors_module

    try:
        spec = importlib.util.spec_from_file_location(
            "orcheo_sdk.cli.workflow.frontmatter",
            module_path,
        )
        assert spec is not None and spec.loader is not None

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for module_name in ("orcheo_sdk", "orcheo_sdk.cli", "orcheo_sdk.cli.errors"):
            sys.modules.pop(module_name, None)
        if "orcheo_sdk.cli.workflow.frontmatter" in saved_modules:
            sys.modules["orcheo_sdk.cli.workflow.frontmatter"] = saved_modules[
                "orcheo_sdk.cli.workflow.frontmatter"
            ]
        else:
            sys.modules.pop("orcheo_sdk.cli.workflow.frontmatter", None)
        for module_name, module in saved_modules.items():
            if module_name == "orcheo_sdk.cli.workflow.frontmatter":
                continue
            sys.modules[module_name] = module


frontmatter = _load_frontmatter_module()


def test_workflow_frontmatter_is_empty_by_default() -> None:
    """A default dataclass instance should report as empty."""
    assert frontmatter.WorkflowFrontmatter().is_empty


def test_workflow_frontmatter_is_not_empty_when_populated() -> None:
    """Any populated field should make the dataclass non-empty."""
    assert not frontmatter.WorkflowFrontmatter(name="x").is_empty
    assert not frontmatter.WorkflowFrontmatter(description="x").is_empty


def test_parse_returns_empty_when_no_block() -> None:
    source = "# regular comment\nprint('hello')\n"
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm == frontmatter.WorkflowFrontmatter()
    assert fm.is_empty


def test_parse_extracts_all_fields() -> None:
    source = (
        "# /// orcheo\n"
        '# name = "My Workflow"\n'
        '# id = "wf-abc123"\n'
        '# description = "Human summary"\n'
        '# config = "./wf.config.json"\n'
        '# entrypoint = "build_graph"\n'
        "# ///\n"
        "print('hello')\n"
    )
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.name == "My Workflow"
    assert fm.workflow_id == "wf-abc123"
    assert fm.description == "Human summary"
    assert fm.config_path == "./wf.config.json"
    assert fm.entrypoint == "build_graph"
    assert not fm.is_empty


def test_parse_accepts_handle_alias() -> None:
    source = '# /// orcheo\n# handle = "wf-handle"\n# ///\n'
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.workflow_id is None
    assert fm.workflow_handle == "wf-handle"


def test_parse_rejects_id_and_handle_together() -> None:
    source = '# /// orcheo\n# id = "x"\n# handle = "y"\n# ///\n'
    with pytest.raises(frontmatter.CLIError, match="must not specify both"):
        frontmatter.parse_workflow_frontmatter(source)


def test_parse_rejects_unknown_field() -> None:
    source = '# /// orcheo\n# bogus = "x"\n# ///\n'
    with pytest.raises(
        frontmatter.CLIError, match="Unknown 'orcheo' frontmatter field"
    ):
        frontmatter.parse_workflow_frontmatter(source)


def test_parse_rejects_non_string_field() -> None:
    source = "# /// orcheo\n# name = 123\n# ///\n"
    with pytest.raises(frontmatter.CLIError, match="must be a string"):
        frontmatter.parse_workflow_frontmatter(source)


def test_parse_rejects_empty_string_field() -> None:
    source = '# /// orcheo\n# name = "   "\n# ///\n'
    with pytest.raises(frontmatter.CLIError, match="must not be empty"):
        frontmatter.parse_workflow_frontmatter(source)


def test_parse_rejects_invalid_toml() -> None:
    source = '# /// orcheo\n# name = "unterminated\n# ///\n'
    with pytest.raises(frontmatter.CLIError, match="Invalid TOML"):
        frontmatter.parse_workflow_frontmatter(source)


def test_parse_rejects_unterminated_orcheo_block() -> None:
    source = '# /// orcheo\n# name = "Missing End"\n'
    with pytest.raises(frontmatter.CLIError, match="Unterminated 'orcheo' frontmatter"):
        frontmatter.parse_workflow_frontmatter(source)


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
    with pytest.raises(
        frontmatter.CLIError, match="Multiple 'orcheo' frontmatter blocks"
    ):
        frontmatter.parse_workflow_frontmatter(source)


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
    fm = frontmatter.parse_workflow_frontmatter(source)
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
    fm = frontmatter.parse_workflow_frontmatter(source)
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
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.name == "Mixed Endings"
    assert fm.workflow_id == "wf-mixed"


def test_parse_handles_empty_comment_lines() -> None:
    """Blank and malformed comment lines are ignored."""
    source = '# /// orcheo\n#\n# name = "Robust Parsing"\n\n# ///\n'
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.name == "Robust Parsing"


def test_parse_handles_comment_only_lines() -> None:
    """Lines that only contain '#' should be skipped."""
    source = '# /// orcheo\n#\n# name = "Comment Only Test"\n#\n# ///\n'
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.name == "Comment Only Test"


def test_parse_handles_tabs_in_comment() -> None:
    """Tabs after ``#`` should still parse as TOML lines."""
    source = '# /// orcheo\n#\tname = "Tab Test"\n# ///\n'
    fm = frontmatter.parse_workflow_frontmatter(source)
    assert fm.name == "Tab Test"


def test_encoding_from_cookie_returns_none_for_non_ascii_bytes() -> None:
    """Non-ASCII cookie lines are ignored instead of raising."""
    assert frontmatter._encoding_from_cookie(b"\xff# coding: latin-1") is None


def test_encoding_from_cookie_reads_codec_cookie() -> None:
    """ASCII cookie lines should return the declared encoding."""
    assert frontmatter._encoding_from_cookie(b"# coding: latin-1") == "latin-1"


def test_detect_file_encoding_prefers_utf8_sig_bom(tmp_path: Path) -> None:
    """UTF-8 BOMs should be detected as ``utf-8-sig``."""
    py_file = tmp_path / "wf_bom.py"
    py_file.write_bytes(
        codecs.BOM_UTF8 + b'# /// orcheo\n# name = "BOM Workflow"\n# ///\n'
    )

    assert frontmatter._detect_file_encoding(py_file) == "utf-8-sig"


def test_detect_file_encoding_handles_utf16_bom(tmp_path: Path) -> None:
    """A decodable UTF-16 BOM should round-trip to ``utf-16``."""
    py_file = tmp_path / "wf_utf16.py"
    py_file.write_text(
        '# /// orcheo\n# name = "UTF-16 Workflow"\n# ///\n',
        encoding="utf-16",
    )

    assert frontmatter._detect_file_encoding(py_file) == "utf-16"


def test_detect_file_encoding_falls_back_when_bom_is_not_decodable(
    tmp_path: Path,
) -> None:
    """Broken BOM-backed files should fall back instead of crashing."""
    py_file = tmp_path / "wf_broken_utf16.py"
    py_file.write_bytes(codecs.BOM_UTF16_LE + b"\xff")

    assert frontmatter._detect_file_encoding(py_file) == "utf-8"


def test_detect_file_encoding_uses_first_line_cookie(tmp_path: Path) -> None:
    """PEP 263 cookies on the first line should be honored."""
    py_file = tmp_path / "wf_cookie_first.py"
    py_file.write_text(
        "# -*- coding: latin-1 -*-\nprint('café')\n",
        encoding="latin-1",
    )

    assert frontmatter._detect_file_encoding(py_file) == "latin-1"


def test_detect_file_encoding_uses_second_line_cookie(tmp_path: Path) -> None:
    """PEP 263 cookies on the second line should also be honored."""
    py_file = tmp_path / "wf_cookie_second.py"
    py_file.write_text(
        "#!/usr/bin/env python3\n# coding=utf-16\nprint('hello')\n",
        encoding="utf-16",
    )

    assert frontmatter._detect_file_encoding(py_file) == "utf-16"


def test_detect_file_encoding_defaults_to_utf8(tmp_path: Path) -> None:
    """Files without an encoding declaration should default to UTF-8."""
    py_file = tmp_path / "wf_no_cookie.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")

    assert frontmatter._detect_file_encoding(py_file) == "utf-8"


def test_detect_file_encoding_handles_non_ascii_first_line(tmp_path: Path) -> None:
    """Non-ASCII bytes on the first line should fall back to UTF-8."""
    py_file = tmp_path / "wf_non_ascii_first.py"
    with py_file.open("wb") as f:
        f.write(b"\xff\xfe\x00\x00\n# coding: latin-1\n")

    assert frontmatter._detect_file_encoding(py_file) == "utf-8"


def test_detect_file_encoding_returns_utf8_for_missing_file(tmp_path: Path) -> None:
    """Unreadable files should fall back to UTF-8."""
    missing_file = tmp_path / "missing.py"
    assert frontmatter._detect_file_encoding(missing_file) == "utf-8"


def test_resolve_config_accepts_absolute_path(tmp_path: Path) -> None:
    """Absolute config paths should skip workflow-relative resolution."""
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    config = tmp_path / "absolute.config.json"
    config.write_text(json.dumps({"tags": ["absolute"]}), encoding="utf-8")

    data = frontmatter.resolve_frontmatter_config(workflow, str(config))
    assert data == {"tags": ["absolute"]}


def test_resolve_config_relative_to_workflow(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    config = tmp_path / "wf.config.json"
    config.write_text(json.dumps({"tags": ["alpha"]}), encoding="utf-8")

    data = frontmatter.resolve_frontmatter_config(workflow, "wf.config.json")
    assert data == {"tags": ["alpha"]}


def test_resolve_config_missing_file_raises(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")

    with pytest.raises(frontmatter.CLIError, match="does not exist"):
        frontmatter.resolve_frontmatter_config(workflow, "missing.config.json")


def test_resolve_config_rejects_directory(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    (tmp_path / "configdir").mkdir()

    with pytest.raises(frontmatter.CLIError, match="is not a file"):
        frontmatter.resolve_frontmatter_config(workflow, "configdir")


def test_resolve_config_rejects_invalid_json(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")

    with pytest.raises(frontmatter.CLIError, match="Invalid JSON"):
        frontmatter.resolve_frontmatter_config(workflow, "bad.json")


def test_resolve_config_rejects_non_object(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.py"
    workflow.write_text("# noop", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(frontmatter.CLIError, match="must contain a JSON object"):
        frontmatter.resolve_frontmatter_config(workflow, "arr.json")


def test_load_from_file_reads_source(tmp_path: Path) -> None:
    py_file = tmp_path / "wf.py"
    py_file.write_text(
        '# /// orcheo\n# name = "From File"\n# ///\n',
        encoding="utf-8",
    )
    fm = frontmatter.load_workflow_frontmatter(py_file)
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
        "print('café')\n"
    )
    py_file.write_text(content, encoding="latin-1")

    fm = frontmatter.load_workflow_frontmatter(py_file)
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

    fm = frontmatter.load_workflow_frontmatter(py_file)
    assert fm.name == "Second Line Encoding"


def test_load_from_file_defaults_to_utf8(tmp_path: Path) -> None:
    """Files without encoding declaration default to UTF-8."""
    py_file = tmp_path / "wf_no_encoding.py"
    content = "# /// orcheo\n# name = \"No Encoding Declared\"\n# ///\nprint('hello')\n"
    py_file.write_text(content, encoding="utf-8")

    fm = frontmatter.load_workflow_frontmatter(py_file)
    assert fm.name == "No Encoding Declared"


def test_load_from_file_with_file_not_found_error(tmp_path: Path) -> None:
    """load_workflow_frontmatter raises CLIError for missing files."""
    missing_file = tmp_path / "does_not_exist.py"
    with pytest.raises(frontmatter.CLIError, match="Failed to read workflow file"):
        frontmatter.load_workflow_frontmatter(missing_file)


def test_load_from_file_with_invalid_encoding(tmp_path: Path) -> None:
    """load_workflow_frontmatter handles decoding errors gracefully."""
    py_file = tmp_path / "bad_encoding.py"
    with py_file.open("wb") as f:
        f.write(b"# -*- coding: utf-8 -*-\n")
        f.write(b"# /// orcheo\n")
        f.write(b'# name = "\xff\xfe"  # Invalid UTF-8 bytes\n')
        f.write(b"# ///\n")

    with pytest.raises(frontmatter.CLIError, match="Failed to decode workflow file"):
        frontmatter.load_workflow_frontmatter(py_file)


def test_is_schema_declaration_requires_explicit_schema_keys() -> None:
    """Objects with ambiguous keys alone should remain runtime config values."""
    assert frontmatter._is_schema_declaration({"type": "provider"}) is False
    assert frontmatter._is_schema_declaration({"pattern": "^openai"}) is False


def test_is_schema_declaration_accepts_inline_schema_annotation() -> None:
    """Inline schema annotations with explicit schema keys are detected."""
    assert (
        frontmatter._is_schema_declaration(
            {"type": "string", "enum": ["a", "b"], "default": "a"}
        )
        is True
    )


def test_is_schema_declaration_rejects_non_mapping_values() -> None:
    """Non-mapping values are never treated as schema declarations."""
    assert frontmatter._is_schema_declaration("nope") is False


def test_is_schema_declaration_rejects_mappings_without_schema_keys() -> None:
    """Plain mappings without schema keys should not be treated as annotations."""
    assert frontmatter._is_schema_declaration({"value": "plain"}) is False


def test_resolve_frontmatter_config_bundle_merges_inline_and_sibling_schema(
    tmp_path: Path,
) -> None:
    """Inline schema defaults are resolved while sibling schema defs override metadata."""
    workflow = tmp_path / "workflow.py"
    workflow.write_text("# noop\n", encoding="utf-8")
    config_path = tmp_path / "workflow.config.json"
    config_path.write_text(
        json.dumps(
            {
                "configurable": {
                    "mode": {
                        "type": "string",
                        "default": "inline",
                    },
                    "count": {
                        "type": "integer",
                        "default": 3,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    schema_path = tmp_path / "workflow.config.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "configurable": {
                    "mode": {
                        "type": "string",
                        "default": "schema",
                    },
                    "extra": {
                        "type": "string",
                        "default": "from-schema",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    raw_config, schema_definitions = frontmatter.resolve_frontmatter_config_bundle(
        workflow,
        "workflow.config.json",
    )

    assert raw_config == {
        "configurable": {
            "mode": "inline",
            "count": 3,
        }
    }
    assert schema_definitions == {
        "mode": {"type": "string", "default": "schema"},
        "count": {"type": "integer", "default": 3},
        "extra": {"type": "string", "default": "from-schema"},
    }


def test_resolve_frontmatter_config_bundle_rejects_schema_directory(
    tmp_path: Path,
) -> None:
    """A sibling schema path must be a file, not a directory."""
    workflow = tmp_path / "workflow.py"
    workflow.write_text("# noop\n", encoding="utf-8")
    config_path = tmp_path / "workflow.config.json"
    config_path.write_text(json.dumps({"configurable": {}}), encoding="utf-8")
    (tmp_path / "workflow.config.schema.json").mkdir()

    with pytest.raises(frontmatter.CLIError, match="is not a file"):
        frontmatter.resolve_frontmatter_config_bundle(workflow, "workflow.config.json")


def test_resolve_frontmatter_config_bundle_rejects_invalid_schema_json(
    tmp_path: Path,
) -> None:
    """Invalid JSON in the sibling schema file should surface as CLIError."""
    workflow = tmp_path / "workflow.py"
    workflow.write_text("# noop\n", encoding="utf-8")
    config_path = tmp_path / "workflow.config.json"
    config_path.write_text(json.dumps({"configurable": {}}), encoding="utf-8")
    schema_path = tmp_path / "workflow.config.schema.json"
    schema_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(
        frontmatter.CLIError, match="Invalid JSON in frontmatter schema file"
    ):
        frontmatter.resolve_frontmatter_config_bundle(workflow, "workflow.config.json")


def test_resolve_frontmatter_config_bundle_rejects_non_object_schema_payload(
    tmp_path: Path,
) -> None:
    """Schema files must contain JSON objects."""
    workflow = tmp_path / "workflow.py"
    workflow.write_text("# noop\n", encoding="utf-8")
    config_path = tmp_path / "workflow.config.json"
    config_path.write_text(json.dumps({"configurable": {}}), encoding="utf-8")
    schema_path = tmp_path / "workflow.config.schema.json"
    schema_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(
        frontmatter.CLIError,
        match="must contain a JSON object",
    ):
        frontmatter.resolve_frontmatter_config_bundle(workflow, "workflow.config.json")


def test_split_annotated_config_returns_original_when_unannotated() -> None:
    """Configs without annotated values should pass through unchanged."""
    config = {"configurable": {"plain": "value"}}

    raw_config, schema_definitions = frontmatter._split_annotated_config(
        config,
        config_path="workflow.config.json",
    )

    assert raw_config == config
    assert schema_definitions is None


def test_split_annotated_config_can_skip_non_mapping_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should skip values that fail the mapping guard."""
    monkeypatch.setattr(frontmatter, "_is_schema_declaration", lambda value: True)

    config = {"configurable": {"broken": 1}}
    raw_config, schema_definitions = frontmatter._split_annotated_config(
        config,
        config_path="workflow.config.json",
    )

    assert raw_config == config
    assert schema_definitions is None


def test_split_annotated_config_resolves_schema_defaults() -> None:
    """Annotated values should be replaced with their runtime defaults."""
    config = {
        "configurable": {
            "mode": {"type": "string", "default": "draft"},
            "variant": {"type": "string", "const": "stable"},
            "choice": {"type": "string", "enum": ["alpha", "beta"]},
            "plain": "keep",
        }
    }

    raw_config, schema_definitions = frontmatter._split_annotated_config(
        config,
        config_path="workflow.config.json",
    )

    assert raw_config == {
        "configurable": {
            "mode": "draft",
            "variant": "stable",
            "choice": "alpha",
            "plain": "keep",
        }
    }
    assert schema_definitions == {
        "mode": {"type": "string", "default": "draft"},
        "variant": {"type": "string", "const": "stable"},
        "choice": {"type": "string", "enum": ["alpha", "beta"]},
    }


def test_normalize_schema_definition_map_uses_payload_when_configurable_missing() -> (
    None
):
    """Schema payloads without a configurable wrapper are normalized directly."""
    payload = {"mode": {"type": "string", "default": "draft"}}

    assert (
        frontmatter._normalize_schema_definition_map(payload, "schema.json") == payload
    )


def test_normalize_schema_definition_map_rejects_non_object_fields() -> None:
    """Every schema field must be a JSON object."""
    with pytest.raises(frontmatter.CLIError, match="must be an object"):
        frontmatter._normalize_schema_definition_map({"mode": 1}, "schema.json")


def test_merge_schema_definitions_prefers_later_entries() -> None:
    """Later schema maps should overwrite earlier keys."""
    assert frontmatter._merge_schema_definitions(
        None,
        {"mode": {"default": "inline"}},
        {"mode": {"default": "schema"}, "extra": {"default": "x"}},
    ) == {
        "mode": {"default": "schema"},
        "extra": {"default": "x"},
    }


def test_schema_has_runtime_default_covers_supported_shapes() -> None:
    """Schema defaults can come from default, const, or enum declarations."""
    assert frontmatter._schema_has_runtime_default({"default": 1}) is True
    assert frontmatter._schema_has_runtime_default({"const": "x"}) is True
    assert frontmatter._schema_has_runtime_default({"enum": ["a"]}) is True
    assert frontmatter._schema_has_runtime_default({"enum": []}) is False


def test_normalize_schema_definition_requires_runtime_default() -> None:
    """Schema metadata without a runtime default should fail fast."""
    with pytest.raises(frontmatter.CLIError, match="no runtime default"):
        frontmatter._normalize_schema_definition(
            {"type": "string"},
            key="mode",
            config_path="workflow.config.json",
        )


def test_resolve_schema_default_covers_all_supported_defaults() -> None:
    """Runtime defaults resolve from default, const, or enum in that order."""
    assert (
        frontmatter._resolve_schema_default(
            {"default": "draft"},
            key="mode",
            config_path="workflow.config.json",
        )
        == "draft"
    )
    assert (
        frontmatter._resolve_schema_default(
            {"const": "stable"},
            key="mode",
            config_path="workflow.config.json",
        )
        == "stable"
    )
    assert (
        frontmatter._resolve_schema_default(
            {"enum": ["alpha", "beta"]},
            key="mode",
            config_path="workflow.config.json",
        )
        == "alpha"
    )


def test_resolve_schema_default_raises_without_runtime_default() -> None:
    """Missing runtime defaults should raise a CLIError."""
    with pytest.raises(frontmatter.CLIError, match="no runtime default"):
        frontmatter._resolve_schema_default(
            {},
            key="mode",
            config_path="workflow.config.json",
        )
