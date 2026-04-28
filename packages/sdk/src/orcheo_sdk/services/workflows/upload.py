"""Workflow upload helpers."""

from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.http import ApiClient


if TYPE_CHECKING:  # pragma: no cover - typing only
    from orcheo_sdk.cli.workflow.frontmatter import WorkflowFrontmatter


def _load_workflow_config_from_path(
    path_obj: Path,
    *,
    load_python: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Load a workflow configuration from a Python source file."""
    file_extension = path_obj.suffix.lower()
    if file_extension != ".py":
        raise CLIError(
            f"Unsupported file type '{file_extension}'. Only .py files are supported."
        )

    try:
        return load_python(path_obj)
    except CLIError:
        raise
    except Exception as exc:  # pragma: no cover - defensive error context
        raise CLIError(
            f"Failed to load workflow definition from '{path_obj}'."
        ) from exc


def _upload_langgraph_workflow(
    state: Any,
    workflow_config: dict[str, Any],
    workflow_id: str | None,
    path_obj: Path,
    requested_name: str | None,
    *,
    uploader: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Upload a LangGraph workflow script via CLI helper."""
    try:
        return uploader(
            state,
            workflow_config,
            workflow_id,
            path_obj,
            requested_name,
        )
    except CLIError:
        raise
    except Exception as exc:  # pragma: no cover - defensive error context
        raise CLIError("Failed to upload LangGraph workflow script via API.") from exc


def _apply_frontmatter_defaults(
    *,
    path_obj: Path,
    frontmatter: WorkflowFrontmatter,
    workflow_id: str | None,
    workflow_name: str | None,
    entrypoint: str | None,
    runnable_config: dict[str, Any] | None,
    console: Any | None,
) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
    """Fill missing values from the workflow file's frontmatter.

    CLI-provided arguments always take precedence; frontmatter only fills
    in fields that were omitted by the caller.
    """
    from orcheo_sdk.cli.workflow.frontmatter import resolve_frontmatter_config

    if frontmatter.is_empty:
        return workflow_id, workflow_name, entrypoint, runnable_config

    used: list[str] = []
    if workflow_id is None and frontmatter.workflow_id is not None:
        workflow_id = frontmatter.workflow_id
        used.append("id")
    if workflow_name is None and frontmatter.name is not None:
        workflow_name = frontmatter.name
        used.append("name")
    if entrypoint is None and frontmatter.entrypoint is not None:
        entrypoint = frontmatter.entrypoint
        used.append("entrypoint")
    if runnable_config is None and frontmatter.config_path is not None:
        runnable_config = resolve_frontmatter_config(path_obj, frontmatter.config_path)
        used.append(f"config ({frontmatter.config_path})")

    if used and console is not None:
        try:
            console.print(f"[dim]Loaded workflow frontmatter: {', '.join(used)}.[/dim]")
        except Exception:  # noqa: BLE001  # pragma: no cover - defensive
            pass

    return workflow_id, workflow_name, entrypoint, runnable_config


def upload_workflow_data(
    client: ApiClient,
    file_path: str | Path,
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    entrypoint: str | None = None,
    runnable_config: dict[str, Any] | None = None,
    console: Any | None = None,
) -> dict[str, Any]:
    """Upload workflow definition from a local file."""
    from orcheo_sdk.cli.workflow import (
        _load_workflow_from_python,
        _normalize_workflow_name,
        _upload_langgraph_script,
        _validate_local_path,
    )
    from orcheo_sdk.cli.workflow.frontmatter import load_workflow_frontmatter

    class MinimalState:
        def __init__(self, client_obj: Any, console_obj: Any | None) -> None:
            self.client = client_obj
            self.console = console_obj or _FakeConsole()

    class _FakeConsole:
        def print(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - noop
            pass

    state = MinimalState(client, console)
    path_obj = _validate_local_path(file_path, description="workflow")

    frontmatter = load_workflow_frontmatter(path_obj)
    (
        workflow_id,
        workflow_name,
        entrypoint,
        runnable_config,
    ) = _apply_frontmatter_defaults(
        path_obj=path_obj,
        frontmatter=frontmatter,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        entrypoint=entrypoint,
        runnable_config=runnable_config,
        console=console,
    )

    requested_name = _normalize_workflow_name(workflow_name)

    workflow_config = _load_workflow_config_from_path(
        path_obj,
        load_python=_load_workflow_from_python,
    )

    if workflow_config.get("_type") != "langgraph_script":
        msg = "Only LangGraph Python scripts can be uploaded."
        raise CLIError(msg)
    if entrypoint:
        workflow_config["entrypoint"] = entrypoint
    if runnable_config is not None:
        workflow_config["runnable_config"] = runnable_config
    result = _upload_langgraph_workflow(
        state,  # type: ignore[arg-type]
        workflow_config,
        workflow_id,
        path_obj,
        requested_name,
        uploader=_upload_langgraph_script,
    )

    return result
