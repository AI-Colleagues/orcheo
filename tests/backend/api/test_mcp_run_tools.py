"""End-to-end tests for the MCP run tools."""

from __future__ import annotations
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.dependencies import get_history_store, get_repository
from tests.backend.api.mcp_support import SIMPLE_SCRIPT, McpCaller, mcp_session


async def _workflow_with_two_versions(mcp: McpCaller) -> str:
    first = await mcp.call("upload_workflow", {"script": SIMPLE_SCRIPT, "name": "R"})
    workflow_id = first["workflow"]["id"]
    await mcp.call(
        "upload_workflow", {"script": SIMPLE_SCRIPT, "workflow": workflow_id}
    )
    return workflow_id


@pytest.mark.asyncio()
async def test_run_list_get_and_cancel(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        workflow_id = await _workflow_with_two_versions(mcp)
        latest_run = await mcp.call(
            "run_workflow", {"workflow": workflow_id, "inputs": {"topic": "ai"}}
        )
        pinned_run = await mcp.call(
            "run_workflow", {"workflow": workflow_id, "version": 1}
        )
        runs = await mcp.call(
            "list_workflow_runs", {"workflow": workflow_id, "limit": 5}
        )
        fetched = await mcp.call("get_run", {"run_id": latest_run["id"]})
        cancelled = await mcp.call(
            "cancel_run", {"run_id": latest_run["id"], "reason": "no longer needed"}
        )

    versions = api_client.get(f"/api/workflows/{workflow_id}/versions").json()
    version_ids = {entry["version"]: entry["id"] for entry in versions}
    assert latest_run["status"] == "pending"
    assert latest_run["input_payload"] == {"topic": "ai"}
    assert latest_run["workflow_version_id"] == version_ids[2]
    assert latest_run["triggered_by"] == "mcp"
    assert pinned_run["workflow_version_id"] == version_ids[1]
    assert {run["id"] for run in runs["runs"]} == {latest_run["id"], pinned_run["id"]}
    assert fetched["id"] == latest_run["id"]
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio()
async def test_run_reports_exhausted_quota(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = api_client.app.dependency_overrides[get_repository]()

    async def _no_runs(_request: object) -> list:
        return []

    async with mcp_session(api_client.app) as mcp:
        workflow_id = await _workflow_with_two_versions(mcp)
        monkeypatch.setattr(repository, "dispatch_manual_runs", _no_runs)
        result = await mcp.call("run_workflow", {"workflow": workflow_id})

    assert result["status"] == "skipped"


@pytest.mark.asyncio()
async def test_run_rejects_unknown_workflow(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error("run_workflow", {"workflow": str(uuid4())})

    assert "HTTP 404" in error


@pytest.mark.asyncio()
async def test_get_run_trace(api_client: TestClient) -> None:
    history_store = api_client.app.dependency_overrides[get_history_store]()
    async with mcp_session(api_client.app) as mcp:
        workflow_id = await _workflow_with_two_versions(mcp)
        run = await mcp.call("run_workflow", {"workflow": workflow_id})
        await history_store.start_run(workflow_id=workflow_id, execution_id=run["id"])
        trace = await mcp.call("get_run_trace", {"run_id": run["id"]})
        missing = await mcp.call_error("get_run_trace", {"run_id": str(uuid4())})

    assert trace["execution"]["id"] == run["id"]
    assert "HTTP 404" in missing


@pytest.mark.asyncio()
async def test_fastmcp_client_round_trip(api_client: TestClient) -> None:
    """A FastMCP client can handshake, list tools and run a workflow."""
    from typing import Any
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from orcheo_backend.app.mcp_server import mcp_lifespan

    app = api_client.app

    def _in_process(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("base_url", None)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    transport = StreamableHttpTransport(
        "http://testserver/api/mcp", httpx_client_factory=_in_process
    )
    async with mcp_lifespan(app), Client(transport) as client:
        init = client.initialize_result
        tools = await client.list_tools()
        await client.call_tool(
            "upload_workflow", {"script": SIMPLE_SCRIPT, "workflow": "ref-flow"}
        )
        run = await client.call_tool("run_workflow", {"workflow": "ref-flow"})

    assert init is not None
    assert init.serverInfo.name == "orcheo"
    assert init.instructions is not None
    assert len(tools) == 31
    assert run.structured_content is not None
    assert run.structured_content["status"] == "pending"
