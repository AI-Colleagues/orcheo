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
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from orcheo_sdk.cli.errors import CLIError


_BLOCK_TYPE = "orcheo"
_FRONTMATTER_RE = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9_-]+)[ \t]*$"
    r"(?P<content>(?:\r?\n^#(?:[ \t].*)?)*?)"
    r"\r?\n^# ///[ \t]*$"
)
_ALLOWED_FIELDS = frozenset({"name", "id", "handle", "config", "entrypoint"})
_ENCODING_RE = re.compile(r"coding[=:]\s*([-\w.]+)")


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
    
    # Check the first line for encoding declaration
    try:
        first_str = first_line.decode("ascii", errors="ignore")
        match = _ENCODING_RE.search(first_str)
        if match:
            return match.group(1)
    except UnicodeDecodeError:
        pass
    
    # Check the second line for encoding declaration
    try:
        second_str = second_line.decode("ascii", errors="ignore")
        match = _ENCODING_RE.search(second_str)
        if match:
            return match.group(1)
    except UnicodeDecodeError:
        pass
    
    return "utf-8"


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
    matches = [
        match
        for match in _FRONTMATTER_RE.finditer(source)
        if match.group("type") == _BLOCK_TYPE
    ]
    if not matches:
        return WorkflowFrontmatter()
    if len(matches) > 1:
        raise CLIError(
            f"Multiple '{_BLOCK_TYPE}' frontmatter blocks found in workflow file."
        )

    content_lines = matches[0].group("content").splitlines()
    toml_lines: list[str] = []
    for line in content_lines:
        stripped = line[1:]  # drop leading '#'
        if stripped.startswith(" "):
            stripped = stripped[1:]
        toml_lines.append(stripped)
    toml_text = "\n".join(toml_lines)

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
    except json.JSONDecodeError as exc:
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
]
