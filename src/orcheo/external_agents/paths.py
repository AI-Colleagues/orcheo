"""Filesystem helpers for managed external agent runtimes."""

from __future__ import annotations
import os
import subprocess
from pathlib import Path
from orcheo.external_agents.models import WorkingDirectoryValidationError


DEFAULT_RUNTIME_DIR_NAME = "agent-runtimes"
DEFAULT_RUNTIME_ROOT_UNDER_DATA = Path("/data") / DEFAULT_RUNTIME_DIR_NAME
DEFAULT_RUNTIME_ROOT_UNDER_HOME = Path("~/.orcheo") / DEFAULT_RUNTIME_DIR_NAME
DEFAULT_WORKSPACE_AGENT_ROOT = Path("/workspace/agents")
RUNTIMES_DIR_NAME = "runtimes"
STAGING_DIR_NAME = "staging"


def default_runtime_root(
    *,
    data_root: Path = Path("/data"),
    home_directory: Path | None = None,
) -> Path:
    """Return the default managed runtime root for the current host."""
    if data_root.exists() and os.access(data_root, os.W_OK | os.X_OK):
        return data_root / DEFAULT_RUNTIME_DIR_NAME

    resolved_home = home_directory if home_directory is not None else Path.home()
    return resolved_home / ".orcheo" / DEFAULT_RUNTIME_DIR_NAME


def ensure_runtime_root(path: Path | None = None) -> Path:
    """Resolve and create the managed runtime root directory."""
    root = (path or default_runtime_root()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def provider_root(runtime_root: Path, provider: str) -> Path:
    """Return the provider-local directory under ``runtime_root``."""
    return runtime_root / provider


def provider_manifest_path(runtime_root: Path, provider: str) -> Path:
    """Return the manifest path for ``provider``."""
    return provider_root(runtime_root, provider) / "manifest.json"


def provider_environment_path(
    runtime_root: Path,
    provider: str,
    *,
    workspace_id: str | None = None,
) -> Path:
    """Return the persisted provider environment path for ``provider``."""
    if workspace_id is None:
        return provider_root(runtime_root, provider) / "environment.json"
    return (
        provider_root(runtime_root, provider)
        / "workspaces"
        / workspace_id
        / "environment.json"
    )


def workspace_provider_environment_overrides(
    provider: str,
    *,
    workspace_id: str | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, str]:
    """Return workspace-specific auth cache roots for provider commands."""
    if workspace_id is None or not workspace_id.strip():
        return {}

    root = (
        Path(workspace_root).expanduser()
        if workspace_root is not None
        else DEFAULT_WORKSPACE_AGENT_ROOT / workspace_id.strip()
    )
    resolved_root = root.resolve(strict=False)
    if provider == "codex":
        return {"CODEX_HOME": str(resolved_root / ".codex")}
    if provider == "gemini":
        return {"HOME": str(resolved_root)}
    return {}


def provider_lock_path(runtime_root: Path, provider: str) -> Path:
    """Return the advisory lock path for ``provider``."""
    return provider_root(runtime_root, provider) / ".lock"


def provider_runtimes_dir(runtime_root: Path, provider: str) -> Path:
    """Return the versioned runtimes directory for ``provider``."""
    return provider_root(runtime_root, provider) / RUNTIMES_DIR_NAME


def provider_staging_dir(runtime_root: Path, provider: str) -> Path:
    """Return the staging directory for ``provider`` installs."""
    return provider_root(runtime_root, provider) / STAGING_DIR_NAME


def _run_git_command(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command for working-directory validation."""
    try:
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = (
            "Git is required to validate external-agent working directories, but "
            "the 'git' executable is not available in this environment."
        )
        raise WorkingDirectoryValidationError(msg) from exc


def _assert_safe_working_directory(
    resolved: Path,
    *,
    runtime_root_resolved: Path,
    home_resolved: Path,
) -> None:
    """Reject unsafe working-directory targets before any git operations."""
    if resolved == Path(resolved.root):
        msg = "Refusing to run an external agent against '/'."
        raise WorkingDirectoryValidationError(msg)
    if resolved == home_resolved:
        msg = "Refusing to run an external agent against the worker home directory."
        raise WorkingDirectoryValidationError(msg)
    if resolved == runtime_root_resolved or resolved.is_relative_to(
        runtime_root_resolved
    ):
        msg = "Refusing to run an external agent inside the managed runtime root."
        raise WorkingDirectoryValidationError(msg)


def _ensure_directory_exists(resolved: Path, *, auto_init_git_worktree: bool) -> None:
    """Create or validate the requested working directory before git checks."""
    if resolved.exists():
        if not resolved.is_dir():
            msg = f"Working directory '{resolved}' is not a directory."
            raise WorkingDirectoryValidationError(msg)
        return
    if not auto_init_git_worktree:
        msg = f"Working directory '{resolved}' does not exist."
        raise WorkingDirectoryValidationError(msg)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Unable to create working directory '{resolved}': {exc}"
        raise WorkingDirectoryValidationError(msg) from exc


def _ensure_git_worktree(resolved: Path, *, auto_init_git_worktree: bool) -> None:
    """Require a git worktree, optionally initializing one in place."""
    git_check = _run_git_command(resolved, "rev-parse", "--show-toplevel")
    if git_check.returncode == 0:
        return
    if auto_init_git_worktree:
        init_result = _run_git_command(
            resolved,
            "init",
            "--quiet",
            "--initial-branch=main",
        )
        if init_result.returncode != 0:
            msg = f"Failed to initialize a Git worktree in '{resolved}'."
            raise WorkingDirectoryValidationError(msg)
        git_check = _run_git_command(resolved, "rev-parse", "--show-toplevel")
        if git_check.returncode == 0:
            return
    msg = (
        f"Working directory '{resolved}' must be a Git worktree root or a "
        "descendant inside a Git worktree."
    )
    raise WorkingDirectoryValidationError(msg)


def validate_working_directory(
    candidate: str | Path,
    *,
    runtime_root: Path,
    home_directory: Path | None = None,
    workspace_id: str | None = None,
    workspace_root: str | Path | None = None,
    auto_init_git_worktree: bool = False,
) -> Path:
    """Validate a requested working directory for external agent execution."""
    candidate_path = Path(candidate).expanduser()
    resolved = candidate_path.resolve(strict=not auto_init_git_worktree)
    runtime_root_resolved = runtime_root.expanduser().resolve()
    home_resolved = (
        home_directory.expanduser().resolve()
        if home_directory is not None
        else Path.home().resolve()
    )

    _assert_safe_working_directory(
        resolved,
        runtime_root_resolved=runtime_root_resolved,
        home_resolved=home_resolved,
    )
    if workspace_id is not None:
        workspace_root_path = (
            Path(workspace_root).expanduser()
            if workspace_root is not None
            else DEFAULT_WORKSPACE_AGENT_ROOT / workspace_id
        )
        workspace_root_resolved = workspace_root_path.resolve(
            strict=not auto_init_git_worktree
        )
        if resolved != workspace_root_resolved and not resolved.is_relative_to(
            workspace_root_resolved
        ):
            msg = (
                "Refusing to run an external agent outside the connected "
                f"workspace filesystem root '{workspace_root_resolved}'."
            )
            raise WorkingDirectoryValidationError(msg)
    _ensure_directory_exists(
        resolved,
        auto_init_git_worktree=auto_init_git_worktree,
    )
    _ensure_git_worktree(
        resolved,
        auto_init_git_worktree=auto_init_git_worktree,
    )

    return resolved
