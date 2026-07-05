"""Node execution routes for preview/testing."""

from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from orcheo.nodes.registry import registry
from orcheo_backend.app.dependencies import (
    RepositoryDep,
    resolve_optional_workflow_ref_id,
)
from orcheo_backend.app.schemas.nodes import (
    NodeExecutionRequest,
    NodeExecutionResponse,
)
from orcheo_backend.app.workflow_execution import execute_node


router = APIRouter()

logger = logging.getLogger(__name__)


@router.post(
    "/nodes/execute",
    response_model=NodeExecutionResponse,
)
async def execute_node_endpoint(
    request: NodeExecutionRequest,
    repository: RepositoryDep,
) -> NodeExecutionResponse:
    """Execute a single node in isolation for testing/preview purposes."""
    node_config = request.node_config
    inputs = request.inputs
    workflow_id = await resolve_optional_workflow_ref_id(
        repository, request.workflow_id
    )

    node_type = node_config.get("type")
    if not node_type:
        raise HTTPException(
            status_code=400,
            detail="Node configuration must include a 'type' field",
        )

    node_class = registry.get_node(str(node_type))
    if node_class is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown node type: {node_type}",
        )

    try:
        node_params = {k: v for k, v in node_config.items() if k != "type"}

        result = await execute_node(
            node_class,
            node_params,
            inputs,
            workflow_id=workflow_id,
        )

        node_name = node_params.get("name", "node")
        node_result: Any = None
        if (
            isinstance(result, dict)
            and "node_results" in result
            and node_name in result["node_results"]
        ):
            node_result = result["node_results"][node_name]
        elif isinstance(result, dict) and "messages" in result:  # pragma: no cover
            node_result = result["messages"]

        return NodeExecutionResponse(
            status="success",
            result=node_result,
        )

    except Exception as exc:
        logger.exception("Node execution failed: %s", exc)
        return NodeExecutionResponse(
            status="error",
            error=str(exc),
        )


__all__ = ["router"]
