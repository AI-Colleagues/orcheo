"""MCP tools for inspecting and managing workflows."""

from __future__ import annotations
import logging
import re
from typing import Annotated, Any
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from orcheo.models.workflow_refs import normalize_workflow_handle, workflow_ref_is_uuid
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.cron_extraction import (
    CronExtractionError,
    extract_version_cron_config,
)
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    MCP_ACTOR,
    READ_ONLY,
    WRITE,
    VersionArg,
    WorkflowArg,
    WorkspaceArg,
    api_client,
    fetch_version,
    version_summary,
)
from orcheo_backend.app.mcp_server.api_client import InProcessApiClient, McpApiError


logger = logging.getLogger(__name__)

_MAIN_GUARD = re.compile(r"^\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _strip_main_block(script: str) -> str:
    """Drop everything from an ``if __name__ == "__main__":`` guard onwards."""
    lines = script.split("\n")
    for index, line in enumerate(lines):
        if _MAIN_GUARD.match(line):
            return "\n".join(lines[:index])
    return script


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "workflow"


async def _find_or_create_workflow(
    api: InProcessApiClient,
    *,
    workflow: str | None,
    name: str | None,
    description: str | None,
) -> tuple[dict[str, Any], bool]:
    """Resolve the upload target, creating it when needed.

    A UUID ``workflow`` must already exist. A handle is created on first
    upload, so re-running the same upload is idempotent.
    """
    handle: str | None = None
    if workflow is not None:
        is_uuid = workflow_ref_is_uuid(workflow)
        if not is_uuid:
            try:
                handle = normalize_workflow_handle(workflow)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
        try:
            existing = await api.get(f"/api/workflows/{handle or workflow}")
        except McpApiError as exc:
            if is_uuid or exc.status_code != 404:
                raise
        else:
            if is_uuid or not existing.get("is_archived"):
                return existing, False

    workflow_name = name or handle
    if not workflow_name:
        raise ToolError("Provide 'name' (or a workflow handle) for a new workflow.")
    payload: dict[str, Any] = {
        "name": workflow_name,
        "slug": _slugify(workflow_name),
        "description": description or "LangGraph workflow uploaded via MCP",
        "tags": ["langgraph", "mcp-upload"],
        "actor": MCP_ACTOR,
    }
    if handle is not None:
        payload["handle"] = handle
    return await api.post("/api/workflows", json_body=payload), True


async def _sync_existing_schedule(
    api: InProcessApiClient, workflow_id: str
) -> dict[str, Any] | None:
    """Refresh an existing cron schedule after a new version is ingested."""
    try:
        existing = await api.get(f"/api/workflows/{workflow_id}/triggers/cron/config")
    except McpApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    latest = await fetch_version(api, workflow_id, None)
    try:
        config = extract_version_cron_config(latest)
    except CronExtractionError as exc:
        return {"status": "error", "message": str(exc)}
    if config is None or config == CronTriggerConfig.model_validate(existing):
        return None
    updated = await api.put(
        f"/api/workflows/{workflow_id}/triggers/cron/config",
        json_body=config.model_dump(mode="json"),
    )
    return {"status": "updated", "config": updated}


async def _credential_readiness(
    api: InProcessApiClient, workflow_id: str
) -> dict[str, Any] | None:
    """Return vault readiness for a fresh upload, or None if it can't be checked.

    The upload has already succeeded at this point, so a readiness failure is
    logged rather than reported as a failed tool call.
    """
    try:
        return await api.get(f"/api/workflows/{workflow_id}/credentials/readiness")
    except Exception:  # noqa: BLE001 - informational follow-up only
        logger.warning(
            "Credential readiness check failed after MCP upload of workflow %s",
            workflow_id,
            exc_info=True,
        )
        return None


def register_workflow_tools(server: FastMCP) -> None:
    """Register workflow inspection and lifecycle tools on ``server``."""
    _register_read_tools(server)
    _register_authoring_tools(server)
    _register_lifecycle_tools(server)
    _register_monitoring_tools(server)


def _register_read_tools(server: FastMCP) -> None:
    """Register workflow listing, detail and download tools."""

    @server.tool(annotations=READ_ONLY)
    async def list_workflows(
        include_archived: bool = False,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List workflows in the workspace with publish and schedule status."""
        async with api_client(workspace) as api:
            items = await api.get(
                "/api/workflows", params={"include_archived": include_archived}
            )
        for item in items:
            if isinstance(item.get("latest_version"), dict):
                item["latest_version"] = version_summary(item["latest_version"])
        return {"workflows": items}

    @server.tool(annotations=READ_ONLY)
    async def get_workflow(
        workflow: WorkflowArg,
        version: VersionArg = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Show a workflow with one version's metadata and its five latest runs.

        Use download_workflow to read the version's Python source.
        """
        async with api_client(workspace) as api:
            record = await api.get(f"/api/workflows/{workflow}")
            try:
                selected = version_summary(await fetch_version(api, workflow, version))
            except ToolError:
                if version is not None:
                    raise
                selected = None
            runs = await api.get(f"/api/workflows/{workflow}/runs", params={"limit": 5})
        return {"workflow": record, "version": selected, "recent_runs": runs}

    @server.tool(annotations=READ_ONLY)
    async def list_workflow_versions(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List a workflow's versions (metadata only, newest last)."""
        async with api_client(workspace) as api:
            versions = await api.get(f"/api/workflows/{workflow}/versions")
        return {"versions": [version_summary(entry) for entry in versions]}

    @server.tool(annotations=READ_ONLY)
    async def download_workflow(
        workflow: WorkflowArg,
        version: VersionArg = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Return the Python source and runnable config of a workflow version."""
        async with api_client(workspace) as api:
            selected = await fetch_version(api, workflow, version)
        graph = selected.get("graph") or {}
        source = graph.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ToolError(
                f"Version {selected.get('version')} has no Python source "
                f"(format '{graph.get('format', 'unknown')}'); re-upload the script."
            )
        return {
            "workflow_id": selected.get("workflow_id"),
            "version": selected.get("version"),
            "source": source,
            "runnable_config": selected.get("runnable_config"),
        }


def _register_authoring_tools(server: FastMCP) -> None:
    """Register upload, update and delete tools."""

    @server.tool(annotations=WRITE)
    async def upload_workflow(
        script: Annotated[
            str,
            Field(
                description=(
                    "Full Python source of an Orcheo LangGraph workflow script, "
                    "as accepted by `orcheo workflow upload`."
                )
            ),
        ],
        workflow: Annotated[
            str | None,
            Field(
                description=(
                    "Existing workflow ID, or a handle (created if missing). "
                    "Omit to create a new workflow named 'name'."
                )
            ),
        ] = None,
        name: Annotated[str | None, Field(description="Workflow name.")] = None,
        description: Annotated[
            str | None, Field(description="Workflow description.")
        ] = None,
        entrypoint: Annotated[
            str | None,
            Field(description="Graph builder to use when the script has several."),
        ] = None,
        runnable_config: Annotated[
            dict[str, Any] | None,
            Field(description="Runnable config stored on the new version."),
        ] = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Upload a workflow script as a new version, creating the workflow if needed.

        An existing cron schedule is re-synced when the new version changes it.
        """
        async with api_client(workspace) as api:
            record, created = await _find_or_create_workflow(
                api, workflow=workflow, name=name, description=description
            )
            workflow_id = str(record["id"])
            if not created:
                changes = {
                    key: value
                    for key, value in (("name", name), ("description", description))
                    if value is not None and record.get(key) != value
                }
                if changes:
                    record = await api.put(
                        f"/api/workflows/{workflow_id}",
                        json_body={**changes, "actor": MCP_ACTOR},
                    )
            ingest: dict[str, Any] = {
                "script": _strip_main_block(script),
                "entrypoint": entrypoint,
                "metadata": {"source": "mcp-upload"},
                "notes": "Uploaded via MCP",
                "created_by": MCP_ACTOR,
            }
            if runnable_config is not None:
                ingest["runnable_config"] = runnable_config
            new_version = await api.post(
                f"/api/workflows/{workflow_id}/versions/ingest", json_body=ingest
            )
            schedule = await _sync_existing_schedule(api, workflow_id)
            readiness = await _credential_readiness(api, workflow_id)
        result: dict[str, Any] = {
            "workflow": record,
            "created": created,
            "version": version_summary(new_version),
            "credential_readiness": readiness,
        }
        if schedule is not None:
            result["cron_schedule"] = schedule
        return result

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def update_workflow(
        workflow: WorkflowArg,
        name: Annotated[str | None, Field(description="New name.")] = None,
        handle: Annotated[str | None, Field(description="New handle.")] = None,
        description: Annotated[
            str | None, Field(description="New description.")
        ] = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Rename a workflow or change its handle or description."""
        changes = {
            key: value
            for key, value in (
                ("name", name),
                ("handle", handle),
                ("description", description),
            )
            if value is not None
        }
        if not changes:
            raise ToolError("Provide at least one of name, handle or description.")
        async with api_client(workspace) as api:
            return await api.put(
                f"/api/workflows/{workflow}",
                json_body={**changes, "actor": MCP_ACTOR},
            )

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_workflow(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Archive a workflow so it no longer runs or appears in listings."""
        async with api_client(workspace) as api:
            return await api.delete(
                f"/api/workflows/{workflow}", params={"actor": MCP_ACTOR}
            )


def _register_lifecycle_tools(server: FastMCP) -> None:
    """Register publish, schedule and runnable-config tools."""

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def publish_workflow(
        workflow: WorkflowArg,
        require_login: Annotated[
            bool, Field(description="Require visitors to sign in to chat.")
        ] = False,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Publish a workflow for public ChatKit access and return its share URL."""
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/workflows/{workflow}/publish",
                json_body={"require_login": require_login, "actor": MCP_ACTOR},
            )

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def unpublish_workflow(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Revoke public ChatKit access for a workflow."""
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/workflows/{workflow}/publish/revoke",
                json_body={"actor": MCP_ACTOR},
            )

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def schedule_workflow(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Enable the cron schedule declared by the workflow's latest version."""
        async with api_client(workspace) as api:
            latest = await fetch_version(api, workflow, None)
            try:
                config = extract_version_cron_config(latest)
            except CronExtractionError as exc:
                raise ToolError(str(exc)) from exc
            if config is None:
                return {
                    "status": "noop",
                    "message": f"Workflow '{workflow}' has no cron trigger.",
                }
            applied = await api.put(
                f"/api/workflows/{workflow}/triggers/cron/config",
                json_body=config.model_dump(mode="json"),
            )
        return {"status": "scheduled", "config": applied}

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def unschedule_workflow(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Disable cron-based execution for a workflow."""
        async with api_client(workspace) as api:
            await api.delete(f"/api/workflows/{workflow}/triggers/cron/config")
        return {"status": "unscheduled"}

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def save_workflow_config(
        workflow: WorkflowArg,
        runnable_config: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Runnable config (configurable, tags, metadata, ...). "
                    "Pass null to clear it."
                )
            ),
        ],
        version: VersionArg = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Replace the runnable config stored on a workflow version."""
        async with api_client(workspace) as api:
            target = version
            if target is None:
                target = (await fetch_version(api, workflow, None))["version"]
            updated = await api.put(
                f"/api/workflows/{workflow}/versions/{target}/runnable-config",
                json_body={"runnable_config": runnable_config, "actor": MCP_ACTOR},
            )
        return version_summary(updated)


def _register_monitoring_tools(server: FastMCP) -> None:
    """Register credential readiness and listener tools."""

    @server.tool(annotations=READ_ONLY)
    async def check_workflow_credentials(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Report whether the credentials a workflow references exist in the vault."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/workflows/{workflow}/credentials/readiness")

    @server.tool(annotations=READ_ONLY)
    async def list_workflow_listeners(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List a workflow's listener subscriptions with live health."""
        async with api_client(workspace) as api:
            listeners = await api.get(f"/api/workflows/{workflow}/listeners")
        return {"listeners": listeners}

    async def _set_listener_status(
        workflow: str,
        subscription_id: str,
        action: str,
        workspace: str | None,
    ) -> dict[str, Any]:
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/workflows/{workflow}/listeners/{subscription_id}/{action}",
                json_body={"actor": MCP_ACTOR},
            )

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def pause_workflow_listener(
        workflow: WorkflowArg,
        subscription_id: Annotated[str, Field(description="Listener subscription ID.")],
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Pause one listener subscription (e.g. a Telegram or Slack listener)."""
        return await _set_listener_status(workflow, subscription_id, "pause", workspace)

    @server.tool(annotations=IDEMPOTENT_WRITE)
    async def resume_workflow_listener(
        workflow: WorkflowArg,
        subscription_id: Annotated[str, Field(description="Listener subscription ID.")],
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Resume a paused listener subscription."""
        return await _set_listener_status(
            workflow, subscription_id, "resume", workspace
        )


__all__ = ["register_workflow_tools"]
