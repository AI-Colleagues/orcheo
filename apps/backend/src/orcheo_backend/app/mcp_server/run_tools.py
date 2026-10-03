"""MCP tools for triggering and monitoring workflow runs."""

from __future__ import annotations
from typing import Annotated, Any
from fastmcp import FastMCP
from pydantic import Field
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo_backend.app.mcp_server._shared import (
    MCP_ACTOR,
    READ_ONLY,
    WRITE,
    VersionArg,
    WorkflowArg,
    WorkspaceArg,
    api_client,
    fetch_version,
)
from orcheo_backend.app.mcp_server.scopes import requires


RunIdArg = Annotated[str, Field(description="Workflow run ID.")]


def register_run_tools(server: FastMCP) -> None:
    """Register run execution and monitoring tools on ``server``."""

    @server.tool(annotations=WRITE, tags=requires("workflows:execute"))
    async def run_workflow(
        workflow: WorkflowArg,
        inputs: Annotated[
            dict[str, Any] | None,
            Field(description="Input payload passed to the workflow run."),
        ] = None,
        version: VersionArg = None,
        runnable_config: RunnableConfigModel | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Queue a workflow run and return it without waiting for completion.

        Poll get_run for status and get_run_trace for node-level output.
        """
        async with api_client(workspace) as api:
            # The dispatch endpoint takes a workflow UUID, not a handle.
            record = await api.get(f"/api/workflows/{workflow}")
            item: dict[str, Any] = {"input_payload": inputs or {}}
            if runnable_config is not None:
                item["runnable_config"] = runnable_config.model_dump(
                    mode="json", exclude_unset=True
                )
            if version is not None:
                selected = await fetch_version(api, workflow, version)
                item["workflow_version_id"] = selected["id"]
            runs = await api.post(
                "/api/triggers/manual/dispatch",
                json_body={
                    "workflow_id": record["id"],
                    "actor": MCP_ACTOR,
                    "label": MCP_ACTOR,
                    "runs": [item],
                },
            )
        # A run the workspace quota refuses comes back as HTTP 429, not [].
        return runs[0]

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def list_workflow_runs(
        workflow: WorkflowArg,
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List a workflow's most recent runs, newest first."""
        async with api_client(workspace) as api:
            runs = await api.get(
                f"/api/workflows/{workflow}/runs", params={"limit": limit}
            )
        return {"runs": runs}

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_run(
        run_id: RunIdArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Return a run's status, inputs, output and error."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/runs/{run_id}")

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_run_trace(
        run_id: RunIdArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Return the execution trace (node spans, timings and outputs) of a run."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/executions/{run_id}/trace")

    @server.tool(annotations=WRITE, tags=requires("workflows:execute"))
    async def cancel_run(
        run_id: RunIdArg,
        reason: Annotated[
            str | None, Field(description="Why the run is being cancelled.")
        ] = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Cancel a pending or running workflow run."""
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/runs/{run_id}/cancel",
                json_body={"actor": MCP_ACTOR, "reason": reason},
            )


__all__ = ["register_run_tools"]
