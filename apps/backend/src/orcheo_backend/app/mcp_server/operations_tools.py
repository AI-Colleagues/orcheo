"""MCP tools for execution diagnostics, validation and server capabilities."""

from __future__ import annotations
from typing import Annotated, Any
from uuid import UUID
from fastmcp import FastMCP
from pydantic import Field
from orcheo.agentensor.evaluation import EvaluationRequest
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo_backend.app.mcp_server._shared import (
    IDEMPOTENT_WRITE,
    MCP_ACTOR,
    READ_ONLY,
    WRITE,
    VersionArg,
    WorkflowArg,
    WorkspaceArg,
    api_client,
)
from orcheo_backend.app.mcp_server.api_client import McpApiError
from orcheo_backend.app.mcp_server.scopes import requires


RecordIdArg = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
LimitArg = Annotated[int, Field(ge=1, le=200)]


def register_operations_tools(server: FastMCP) -> None:
    """Register operational controls backed by workspace-aware API routes."""
    _register_history_tools(server)
    _register_health_tools(server)
    _register_evaluation_tools(server)

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def diff_workflow_versions(
        workflow: WorkflowArg,
        base_version: Annotated[int, Field(ge=1)],
        target_version: Annotated[int, Field(ge=1)],
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Compare two stored workflow versions."""
        async with api_client(workspace) as api:
            return await api.get(
                f"/api/workflows/{workflow}/versions/{base_version}/diff/{target_version}"
            )

    @server.tool(annotations=WRITE, tags=requires("workflows:execute"))
    async def execute_node(
        node_config: dict[str, Any],
        inputs: dict[str, Any] | None = None,
        workflow: str | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Execute one node for testing; integrations can have real side effects."""
        async with api_client(workspace) as api:
            return await api.post(
                "/api/nodes/execute",
                json_body={
                    "node_config": node_config,
                    "inputs": inputs or {},
                    "workflow_id": workflow,
                },
            )

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_workflow_listener_metrics(
        workflow: WorkflowArg,
        stall_threshold_seconds: Annotated[int, Field(ge=1, le=3600)] = 180,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read aggregate listener health, stalls and dispatch-failure alerts."""
        async with api_client(workspace) as api:
            return await api.get(
                f"/api/workflows/{workflow}/listeners/metrics",
                params={"stall_threshold_seconds": stall_threshold_seconds},
            )

    @server.tool(annotations=READ_ONLY, tags=requires())
    async def get_server_readiness() -> dict[str, Any]:
        """Check readiness, including Redis connectivity for queued execution."""
        async with api_client() as api:
            return await api.get("/api/system/ready")

    @server.tool(annotations=READ_ONLY, tags=requires())
    async def get_server_features(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """Read feature availability for the selected workspace."""
        async with api_client(workspace) as api:
            return await api.get("/api/system/features")

    @server.tool(annotations=READ_ONLY, tags=requires())
    async def list_server_plugins(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """List runtime plugins and their availability in the workspace."""
        async with api_client(workspace) as api:
            return await api.get("/api/system/plugins")


def _register_history_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def list_workflow_executions(
        workflow: WorkflowArg,
        limit: LimitArg = 20,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List recorded executions, including streaming and evaluation runs."""
        async with api_client(workspace) as api:
            histories = await api.get(
                f"/api/workflows/{workflow}/executions", params={"limit": limit}
            )
        # Full step payloads can be large; fetch them with get_execution_history.
        return {
            "executions": [
                {k: v for k, v in h.items() if k != "steps"} for h in histories
            ]
        }

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_execution_history(
        execution_id: RecordIdArg,
        from_step: Annotated[int, Field(ge=0)] = 0,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read recorded step payloads; from_step slices history without rerunning."""
        async with api_client(workspace) as api:
            if from_step:
                return await api.post(
                    f"/api/executions/{execution_id}/replay",
                    json_body={"from_step": from_step},
                )
            return await api.get(f"/api/executions/{execution_id}/history")


def _register_health_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read", "vault:read"))
    async def get_workflow_credential_health(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read cached credential validation results, without returning secrets."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/workflows/{workflow}/credentials/health")

    @server.tool(annotations=WRITE, tags=requires("workflows:read", "vault:write"))
    async def validate_workflow_credentials(
        workflow: WorkflowArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Validate workflow credentials with their providers and update health."""
        async with api_client(workspace) as api:
            try:
                return await api.post(
                    f"/api/workflows/{workflow}/credentials/validate",
                    json_body={"actor": MCP_ACTOR},
                )
            except McpApiError as exc:
                if (
                    exc.status_code != 422
                    or not isinstance(exc.detail, dict)
                    or "failures" not in exc.detail
                ):
                    raise
                return {
                    "workflow": workflow,
                    "status": "unhealthy",
                    "failures": exc.detail["failures"],
                }

    @server.tool(annotations=READ_ONLY, tags=requires("vault:read"))
    async def list_credential_alerts(
        workflow: str | None = None,
        include_acknowledged: bool = False,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List credential governance alerts in the workspace."""
        async with api_client(workspace) as api:
            alerts = await api.get(
                "/api/credentials/governance-alerts",
                params={
                    "workflow_id": workflow,
                    "include_acknowledged": include_acknowledged,
                },
            )
        return {"alerts": alerts}

    @server.tool(annotations=IDEMPOTENT_WRITE, tags=requires("vault:write"))
    async def acknowledge_credential_alert(
        alert_id: UUID,
        workflow: str | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Acknowledge one credential governance alert."""
        async with api_client(workspace) as api:
            return await api.request(
                "POST",
                f"/api/credentials/governance-alerts/{alert_id}/acknowledge",
                params={"workflow_id": workflow},
                json_body={"actor": MCP_ACTOR},
            )


def _register_evaluation_tools(server: FastMCP) -> None:
    @server.tool(
        annotations=WRITE, tags=requires("workflows:execute", "workflows:write")
    )
    async def evaluate_workflow(
        workflow: WorkflowArg,
        evaluation: EvaluationRequest,
        inputs: dict[str, Any] | None = None,
        runnable_config: RunnableConfigModel | None = None,
        version: VersionArg = None,
        timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 60,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Run an evaluation and wait up to timeout_seconds; persist its history."""
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/workflows/{workflow}/agentensor/evaluate",
                json_body={
                    "evaluation": evaluation.model_dump(mode="json"),
                    "inputs": inputs or {},
                    "version": version,
                    "runnable_config": runnable_config.model_dump(mode="json")
                    if runnable_config is not None
                    else None,
                    "timeout_seconds": timeout_seconds,
                },
            )

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def list_agentensor_checkpoints(
        workflow: WorkflowArg,
        limit: LimitArg = 20,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List stored Agentensor optimization checkpoints."""
        async with api_client(workspace) as api:
            items = await api.get(
                f"/api/workflows/{workflow}/agentensor/checkpoints",
                params={"limit": limit},
            )
        return {"checkpoints": items}

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def get_agentensor_checkpoint(
        workflow: WorkflowArg,
        checkpoint_id: RecordIdArg,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read one Agentensor checkpoint belonging to a workflow."""
        async with api_client(workspace) as api:
            return await api.get(
                f"/api/workflows/{workflow}/agentensor/checkpoints/{checkpoint_id}"
            )
