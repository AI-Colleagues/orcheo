"""Parse optional metadata frontmatter from workflow Python files.

The frontmatter follows the PEP 723-inspired comment block convention:

    # /// orcheo
    # name = "My Workflow"
    # id = "wf-abc123"
    # handle = "my-workflow"
    # description = "Short human-readable summary."
    # config = "./my-workflow.config.json"
    # entrypoint = "build_graph"
    # ///

The block content is parsed as TOML. All fields are optional; CLI flags
always take precedence over values declared in the frontmatter.
"""

from __future__ import annotations
import codecs
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from orcheo_sdk.cli.errors import CLIError


_BLOCK_TYPE = "orcheo"
_BLOCK_START_RE = re.compile(r"^# /// (?P<type>[a-zA-Z0-9_-]+)[ \t]*$")
_BLOCK_END_RE = re.compile(r"^# ///[ \t]*$")
_ALLOWED_FIELDS = frozenset(
    {"name", "id", "handle", "description", "config", "entrypoint"}
)
_ENCODING_RE = re.compile(r"coding[=:]\s*([-\w.]+)")
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "enum",
        "items",
        "properties",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "default",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "additionalProperties",
    }
)
_SCHEMA_DISCRIMINATOR_KEYS = frozenset(
    {
        "enum",
        "items",
        "properties",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "default",
        "additionalProperties",
    }
)


def _encoding_from_cookie(line: bytes) -> str | None:
    """Return a declared source encoding from a single ASCII line."""
    try:
        line_text = line.decode("ascii")
    except UnicodeDecodeError:
        return None
    match = _ENCODING_RE.search(line_text)
    return match.group(1) if match else None


def _encoding_from_bom(path: Path, first_line: bytes) -> str | None:
    """Return a BOM-backed encoding when the file is actually decodable."""
    bom_encodings = (
        ("utf-16", (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)),
        ("utf-32", (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)),
    )
    for encoding, bom_prefixes in bom_encodings:
        if any(first_line.startswith(prefix) for prefix in bom_prefixes):
            try:
                path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                return None
            return encoding
    return None


def _detect_file_encoding(path: Path) -> str:
    """Detect the encoding of a Python source file following PEP 263.

    Returns the encoding name, defaulting to 'utf-8' if none is declared.
    """
    try:
        with path.open("rb") as f:
            # Read the first two lines as bytes to check for encoding declarations
            first_line = f.readline()
            second_line = f.readline()
    except OSError:
        # If we can't read the file, default to utf-8
        return "utf-8"

    encoding: str | None = "utf-8"
    if first_line.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        bom_encoding = _encoding_from_bom(path, first_line)
        if bom_encoding is not None:
            encoding = bom_encoding
        elif any(byte >= 128 for byte in first_line):
            encoding = "utf-8"
        else:
            encoding = _encoding_from_cookie(first_line) or _encoding_from_cookie(
                second_line
            )
            if encoding is None:
                encoding = "utf-8"

    return encoding if encoding is not None else "utf-8"


@dataclass(frozen=True)
class WorkflowFrontmatter:
    """Optional metadata embedded in a workflow source file."""

    name: str | None = None
    workflow_id: str | None = None
    workflow_handle: str | None = None
    description: str | None = None
    config_path: str | None = None
    entrypoint: str | None = None

    @property
    def is_empty(self) -> bool:
        """Return True when no frontmatter values were declared."""
        return not any(
            (
                self.name,
                self.workflow_id,
                self.workflow_handle,
                self.description,
                self.config_path,
                self.entrypoint,
            )
        )


def parse_workflow_frontmatter(source: str) -> WorkflowFrontmatter:
    """Parse the optional ``orcheo`` frontmatter block from Python source."""
    blocks = _collect_frontmatter_blocks(source)

    if not blocks:
        return WorkflowFrontmatter()
    if len(blocks) > 1:
        raise CLIError(
            f"Multiple '{_BLOCK_TYPE}' frontmatter blocks found in workflow file."
        )

    toml_text = "\n".join(_frontmatter_content_to_toml_lines(blocks[0]))

    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise CLIError(f"Invalid TOML in 'orcheo' frontmatter: {exc}") from exc

    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise CLIError(f"Unknown 'orcheo' frontmatter field(s): {keys}.")

    if "id" in data and "handle" in data:
        raise CLIError("'orcheo' frontmatter must not specify both 'id' and 'handle'.")

    return WorkflowFrontmatter(
        name=_string_field(data, "name"),
        workflow_id=_string_field(data, "id"),
        workflow_handle=_string_field(data, "handle"),
        description=_string_field(data, "description"),
        config_path=_string_field(data, "config"),
        entrypoint=_string_field(data, "entrypoint"),
    )


def _collect_frontmatter_blocks(source: str) -> list[list[str]]:
    """Collect all complete frontmatter blocks from a source string."""
    blocks: list[list[str]] = []
    current_lines: list[str] = []
    current_type: str | None = None
    in_block = False

    for line in source.splitlines():
        if in_block:
            if _BLOCK_END_RE.match(line):
                if current_type == _BLOCK_TYPE:
                    blocks.append(current_lines)
                in_block = False
                current_type = None
                current_lines = []
            else:
                current_lines.append(line)
            continue

        match = _BLOCK_START_RE.match(line)
        if match:
            current_type = match.group("type")
            current_lines = []
            in_block = True

    if in_block and current_type == _BLOCK_TYPE:
        raise CLIError(
            f"Unterminated '{_BLOCK_TYPE}' frontmatter block. "
            "Add a closing '# ///' line."
        )

    return blocks


def _frontmatter_content_to_toml_lines(content_lines: list[str]) -> list[str]:
    """Convert comment-prefixed block lines into raw TOML lines."""
    toml_lines: list[str] = []
    for line in content_lines:
        if not line or not line.startswith("#"):
            continue
        stripped = line[1:]
        if stripped.startswith((" ", "\t")):
            stripped = stripped[1:]
        toml_lines.append(stripped)
    return toml_lines


def _string_field(data: dict[str, Any], key: str) -> str | None:
    """Return a normalized string field, or None when absent."""
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, str):
        raise CLIError(f"'orcheo' frontmatter field '{key}' must be a string.")
    stripped = value.strip()
    if not stripped:
        raise CLIError(f"'orcheo' frontmatter field '{key}' must not be empty.")
    return stripped


def load_workflow_frontmatter(path: Path) -> WorkflowFrontmatter:
    """Read ``path`` and return its parsed workflow frontmatter."""
    try:
        encoding = _detect_file_encoding(path)
        source = path.read_text(encoding=encoding)
    except OSError as exc:  # pragma: no cover - filesystem errors
        raise CLIError(f"Failed to read workflow file '{path}': {exc}") from exc
    except UnicodeDecodeError as exc:  # pragma: no cover - encoding errors
        raise CLIError(f"Failed to decode workflow file '{path}': {exc}") from exc
    return parse_workflow_frontmatter(source)


def resolve_frontmatter_config(workflow_path: Path, config_path: str) -> dict[str, Any]:
    """Load a companion runnable config referenced by frontmatter.

    Relative paths resolve against the workflow file's parent directory.
    """
    runnable_config, _ = resolve_frontmatter_config_bundle(workflow_path, config_path)
    return runnable_config


def resolve_frontmatter_config_bundle(
    workflow_path: Path,
    config_path: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Load runnable config and typed schema declarations from frontmatter.

    Relative paths resolve against the workflow file's parent directory.
    """
    candidate = Path(config_path).expanduser()
    if not candidate.is_absolute():
        candidate = workflow_path.parent / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise CLIError(
            f"Frontmatter config file '{config_path}' does not exist "
            f"(resolved to '{resolved}')."
        )
    if not resolved.is_file():
        raise CLIError(f"Frontmatter config path '{config_path}' is not a file.")

    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CLIError(
            f"Invalid JSON in frontmatter config file '{config_path}': {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise CLIError(
            f"Frontmatter config file '{config_path}' must contain a JSON object."
        )

    sibling_schema = _load_sibling_schema_file(resolved)
    raw_config, inline_schema = _split_annotated_config(data, config_path=config_path)
    schema_definitions = _merge_schema_definitions(inline_schema, sibling_schema)
    return raw_config, schema_definitions or None


def _load_sibling_schema_file(resolved_config: Path) -> dict[str, Any] | None:
    """Load an optional ``*.schema.json`` companion file next to the config."""
    schema_path = resolved_config.with_name(
        f"{resolved_config.stem}.schema{resolved_config.suffix}"
    )
    if not schema_path.exists():
        return None
    if not schema_path.is_file():
        raise CLIError(f"Frontmatter schema path '{schema_path.name}' is not a file.")

    try:
        schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CLIError(
            f"Invalid JSON in frontmatter schema file '{schema_path.name}': {exc}"
        ) from exc
    if not isinstance(schema_payload, dict):
        raise CLIError(
            f"Frontmatter schema file '{schema_path.name}' must contain a JSON object."
        )
    return _normalize_schema_definition_map(schema_payload, schema_path.name)


def _split_annotated_config(
    config: dict[str, Any],
    *,
    config_path: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Split annotated runnable config values from their schema definitions."""
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return config, None

    raw_config = dict(config)
    raw_configurable = dict(configurable)
    schema_definitions: dict[str, Any] = {}

    for key, value in configurable.items():
        if not _is_schema_declaration(value):
            continue
        if not isinstance(value, Mapping):
            continue
        schema = _normalize_schema_definition(value, key=key, config_path=config_path)
        raw_configurable[key] = _resolve_schema_default(
            schema,
            key=key,
            config_path=config_path,
        )
        schema_definitions[key] = schema

    if schema_definitions:
        raw_config["configurable"] = raw_configurable
        return raw_config, schema_definitions

    return config, None


def _normalize_schema_definition_map(
    payload: dict[str, Any],
    config_path: str,
) -> dict[str, Any]:
    """Normalize a schema JSON payload into configurable field definitions."""
    configurable = payload.get("configurable")
    if isinstance(configurable, dict):
        source = configurable
    else:
        source = payload

    normalized: dict[str, Any] = {}
    for key, value in source.items():
        if not isinstance(value, Mapping):
            raise CLIError(
                f"Frontmatter schema file '{config_path}' field '{key}' "
                "must be an object."
            )
        normalized[key] = dict(value)
    return normalized


def _merge_schema_definitions(
    *schema_maps: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge schema definition maps with later entries taking precedence."""
    merged: dict[str, Any] = {}
    for schema_map in schema_maps:
        if not schema_map:
            continue
        for key, value in schema_map.items():
            merged[key] = value
    return merged or None


def _is_schema_declaration(value: object) -> bool:
    """Return True when ``value`` is an explicit typed schema annotation."""
    if not isinstance(value, Mapping):
        return False
    if not any(key in value for key in _SCHEMA_KEYS):
        return False
    return any(key in value for key in _SCHEMA_DISCRIMINATOR_KEYS)


def _normalize_schema_definition(
    value: Mapping[str, Any],
    *,
    key: str,
    config_path: str,
) -> dict[str, Any]:
    """Copy a schema declaration into a JSON-serializable mapping."""
    schema = dict(value)
    if not _schema_has_runtime_default(schema):
        raise CLIError(
            f"Frontmatter config field '{key}' in '{config_path}' declares schema "
            "metadata but no runtime default. Add a 'default' value or an 'enum'."
        )
    return schema


def _schema_has_runtime_default(schema: Mapping[str, Any]) -> bool:
    """Return True when a schema declaration can resolve to a runtime value."""
    if "default" in schema:
        return True
    const_value = schema.get("const")
    if const_value is not None:
        return True
    enum_value = schema.get("enum")
    return isinstance(enum_value, list) and len(enum_value) > 0


def _resolve_schema_default(
    schema: Mapping[str, Any],
    *,
    key: str,
    config_path: str,
) -> Any:
    """Return a runtime value from a schema declaration."""
    if "default" in schema:
        return schema["default"]
    if schema.get("const") is not None:
        return schema["const"]
    enum_value = schema.get("enum")
    if isinstance(enum_value, list) and enum_value:
        return enum_value[0]
    raise CLIError(
        f"Frontmatter config field '{key}' in '{config_path}' declares schema "
        "metadata but no runtime default. Add a 'default' value or an 'enum'."
    )


__all__ = [
    "WorkflowFrontmatter",
    "parse_workflow_frontmatter",
    "load_workflow_frontmatter",
    "resolve_frontmatter_config",
    "resolve_frontmatter_config_bundle",
    "_detect_file_encoding",  # Export for testing
]
