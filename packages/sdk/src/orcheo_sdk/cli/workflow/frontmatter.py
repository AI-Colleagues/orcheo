"""Parse optional metadata frontmatter from workflow Python files.

The frontmatter follows the PEP 723-inspired comment block convention:

    # /// orcheo
    # name = "My Workflow"
    # id = "wf-abc123"
    # config = "./my-workflow.config.json"
    # entrypoint = "build_graph"
    # ///

The block content is parsed as TOML.  All fields are optional; CLI flags
always take precedence over values declared in the frontmatter.
"""

from __future__ import annotations
import codecs
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from orcheo_sdk.cli.errors import CLIError


_BLOCK_TYPE = "orcheo"
_BLOCK_START_RE = re.compile(r"^# /// (?P<type>[a-zA-Z0-9_-]+)[ \t]*$")
_BLOCK_END_RE = re.compile(r"^# ///[ \t]*$")
_ALLOWED_FIELDS = frozenset({"name", "id", "handle", "config", "entrypoint"})
_ENCODING_RE = re.compile(r"coding[=:]\s*([-\w.]+)")


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
    config_path: str | None = None
    entrypoint: str | None = None

    @property
    def is_empty(self) -> bool:
        """Return True when no frontmatter values were declared."""
        return not any((self.name, self.workflow_id, self.config_path, self.entrypoint))


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
        workflow_id=_string_field(data, "id") or _string_field(data, "handle"),
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
    return data


__all__ = [
    "WorkflowFrontmatter",
    "parse_workflow_frontmatter",
    "load_workflow_frontmatter",
    "resolve_frontmatter_config",
    "_detect_file_encoding",  # Export for testing
]
