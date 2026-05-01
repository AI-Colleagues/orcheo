"""Helpers for ingesting workflow definitions."""

from __future__ import annotations
import contextlib
import re
import sys
from pathlib import Path
from typing import Any
from orcheo.models.workflow_refs import normalize_workflow_handle
from orcheo.plugins.paths import build_storage_paths
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.state import CLIState


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _generate_slug(value: str) -> str:
    """Generate a slug-safe representation of the given value."""
    normalized = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    fallback = value.strip().lower()
    return normalized or fallback or value


def _normalize_workflow_name(name: str | None) -> str | None:
    """Normalize workflow name input and ensure it is not empty."""
    if name is None:
        return None
    normalized = name.strip()
    if not normalized:
        raise CLIError("Workflow name cannot be empty.")
    return normalized


def _normalize_workflow_handle(value: str | None) -> str | None:
    """Normalize a workflow handle for API compatibility."""
    if value is None:
        return None
    normalized = _generate_slug(value)
    return normalize_workflow_handle(normalized)


def _fetch_workflow_for_upload(
    state: CLIState,
    workflow_ref: str,
    *,
    allow_create_if_missing: bool,
) -> dict[str, Any] | None:
    """Fetch an existing workflow, optionally treating a 404 as missing."""
    try:
        workflow = state.client.get(f"/api/workflows/{workflow_ref}")
        if allow_create_if_missing and workflow.get("is_archived"):
            return None
        return workflow
    except APICallError as exc:
        if allow_create_if_missing and exc.status_code == 404:
            return None
        raise CLIError(f"Failed to fetch workflow '{workflow_ref}': {exc}") from exc
    except Exception as exc:
        raise CLIError(f"Failed to fetch workflow '{workflow_ref}': {exc}") from exc


def _maybe_update_workflow_metadata(
    state: CLIState,
    workflow: dict[str, Any],
    workflow_ref: str,
    *,
    name_override: str | None,
    description_override: str | None,
) -> dict[str, Any]:
    """Update workflow metadata when the caller requested overrides."""
    payload: dict[str, Any] = {}
    if name_override is not None and workflow.get("name") != name_override:
        payload["name"] = name_override
    if (
        description_override is not None
        and workflow.get("description") != description_override
    ):
        payload["description"] = description_override

    if not payload:
        return workflow

    try:
        state.client.put(
            f"/api/workflows/{workflow_ref}",
            json_body=payload,
        )
    except Exception as exc:
        raise CLIError(f"Failed to update workflow '{workflow_ref}': {exc}") from exc

    updated_workflow = dict(workflow)
    updated_workflow.update(payload)
    return updated_workflow


def _create_workflow_for_upload(
    state: CLIState,
    *,
    workflow_name: str,
    workflow_slug: str,
    workflow_handle: str | None,
    workflow_description: str | None,
    path: Path,
) -> dict[str, Any]:
    """Create a workflow record for a new upload."""
    create_payload = {
        "name": workflow_name,
        "slug": workflow_slug,
        "description": workflow_description or f"LangGraph workflow from {path.name}",
        "tags": ["langgraph", "cli-upload"],
        "actor": "cli",
    }
    if workflow_handle is not None:
        create_payload["handle"] = workflow_handle

    try:
        workflow = state.client.post("/api/workflows", json_body=create_payload)
        state.console.print(
            f"[green]Created workflow '{workflow['id']}' ({workflow_name})[/green]"
        )
        return workflow
    except Exception as exc:
        raise CLIError(f"Failed to create workflow: {exc}") from exc


def _build_ingest_payload(
    *,
    script: str,
    entrypoint: str | None,
    path: Path,
    workflow_config: dict[str, Any],
) -> dict[str, Any]:
    """Build the ingestion payload for a LangGraph script upload."""
    payload: dict[str, Any] = {
        "script": script,
        "entrypoint": entrypoint,
        "metadata": {"source": "cli-upload", "filename": path.name},
        "notes": f"Uploaded from {path.name} via CLI",
        "created_by": "cli",
    }
    configurable_schema = workflow_config.get("configurable_schema")
    if isinstance(configurable_schema, dict) and configurable_schema:
        payload["metadata"]["configurable_schema"] = configurable_schema
    runnable_config = workflow_config.get("runnable_config")
    if runnable_config is not None:
        payload["runnable_config"] = runnable_config
    return payload


def _plugin_site_packages() -> Path:
    """Return the managed plugin ``site-packages`` path for the active Python."""
    install_dir = Path(build_storage_paths().install_dir)
    return (
        install_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


@contextlib.contextmanager
def _managed_plugin_sys_path() -> Any:
    """Temporarily expose managed plugin packages during local script loading."""
    site_packages = _plugin_site_packages()
    path_str = str(site_packages)
    should_add = site_packages.exists() and path_str not in sys.path
    if should_add:
        sys.path.insert(0, path_str)
    try:
        yield
    finally:
        if should_add:
            with contextlib.suppress(ValueError):
                sys.path.remove(path_str)


def _upload_langgraph_script(
    state: CLIState,
    workflow_config: dict[str, Any],
    workflow_id: str | None,
    workflow_handle: str | None,
    workflow_description: str | None,
    path: Path,
    name_override: str | None,
) -> dict[str, Any]:
    """Upload a LangGraph script using the ingestion API."""
    script = workflow_config["script"]
    entrypoint = workflow_config.get("entrypoint")

    derived_name = path.stem.replace("_", "-")
    workflow_name = name_override or derived_name
    workflow_slug = _generate_slug(workflow_name) if name_override else derived_name
    normalized_workflow_handle = _normalize_workflow_handle(workflow_handle)
    workflow_ref = workflow_id or normalized_workflow_handle
    allow_create_if_missing = (
        workflow_id is None and normalized_workflow_handle is not None
    )
    workflow = None
    if workflow_ref is not None:
        workflow = _fetch_workflow_for_upload(
            state,
            workflow_ref,
            allow_create_if_missing=allow_create_if_missing,
        )
        if workflow is not None:
            workflow_id = workflow["id"]
            workflow = _maybe_update_workflow_metadata(
                state,
                workflow,
                workflow_ref,
                name_override=name_override,
                description_override=workflow_description,
            )

    if workflow is None:
        workflow = _create_workflow_for_upload(
            state,
            workflow_name=workflow_name,
            workflow_slug=workflow_slug,
            workflow_handle=normalized_workflow_handle,
            workflow_description=workflow_description,
            path=path,
        )
        workflow_id = workflow["id"]

    try:
        version = state.client.post(
            f"/api/workflows/{workflow_id}/versions/ingest",
            json_body=_build_ingest_payload(
                script=script,
                entrypoint=entrypoint,
                path=path,
                workflow_config=workflow_config,
            ),
        )
        state.console.print(
            f"[green]Ingested LangGraph script as version {version['version']}[/green]"
        )
    except Exception as exc:
        raise CLIError(f"Failed to ingest LangGraph script: {exc}") from exc

    workflow["latest_version"] = version
    return workflow


def _strip_main_block(script: str) -> str:
    """Remove if __name__ == '__main__' blocks from Python scripts."""
    lines = script.split("\n")
    filtered_lines = []
    for line in lines:
        if line.strip().startswith('if __name__ == "__main__"'):
            break
        if line.strip().startswith("if __name__ == '__main__'"):
            break
        filtered_lines.append(line)
    return "\n".join(filtered_lines)


def _load_workflow_from_python(path: Path) -> dict[str, Any]:
    """Load a workflow from a Python file."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("workflow_module", path)
    if spec is None or spec.loader is None:
        raise CLIError(f"Failed to load Python module from '{path}'.")

    module = importlib.util.module_from_spec(spec)
    sys.modules["workflow_module"] = module
    try:
        with _managed_plugin_sys_path():
            spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover
        raise CLIError(f"Failed to execute Python file: {exc}") from exc
    finally:
        sys.modules.pop("workflow_module", None)

    if hasattr(module, "workflow"):
        workflow = module.workflow
        if not hasattr(workflow, "to_deployment_payload"):
            msg = "'workflow' variable must be an orcheo_sdk.Workflow instance."
            raise CLIError(msg)

        try:
            return workflow.to_deployment_payload()
        except Exception as exc:  # pragma: no cover
            raise CLIError(f"Failed to generate deployment payload: {exc}") from exc

    try:
        script_content = path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        raise CLIError(f"Failed to read file: {exc}") from exc

    script_content = _strip_main_block(script_content)

    return {
        "_type": "langgraph_script",
        "script": script_content,
        "entrypoint": None,
    }


__all__ = [
    "_generate_slug",
    "_normalize_workflow_name",
    "_upload_langgraph_script",
    "_strip_main_block",
    "_load_workflow_from_python",
]
