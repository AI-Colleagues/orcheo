"""End-to-end tests for the MCP catalog, credential and workspace tools."""

from __future__ import annotations
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.backend.api.mcp_support import mcp_session


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("kind", "name", "query"),
    [
        ("node", "AgentNode", "agentnode"),
        ("edge", "SwitchEdge", "switch"),
        ("agent_tool", "greet_user", "greet"),
    ],
)
async def test_list_and_describe_components(
    api_client: TestClient, kind: str, name: str, query: str
) -> None:
    async with mcp_session(api_client.app) as mcp:
        everything = await mcp.call("list_components", {"kind": kind})
        filtered = await mcp.call("list_components", {"kind": kind, "query": query})
        described = await mcp.call("describe_component", {"kind": kind, "name": name})

    assert len(filtered["components"]) < len(everything["components"])
    assert name in {entry["name"] for entry in filtered["components"]}
    assert described["name"] == name
    assert "properties" in described["schema"]


@pytest.mark.asyncio()
async def test_describe_unknown_component(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error(
            "describe_component", {"kind": "agent_tool", "name": "nope"}
        )

    assert "No agent tool named 'nope' is registered." in error


def test_component_schema_is_optional() -> None:
    from orcheo_backend.app.mcp_server.catalog_tools import _component_schema

    assert _component_schema(object()) is None


@pytest.mark.asyncio()
async def test_credential_tools(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        workflow = await mcp.call(
            "upload_workflow",
            {"script": "def build_graph():\n    pass\n", "name": "Scoped"},
        )
        workflow_id = workflow["workflow"]["id"]
        shared = api_client.post(
            "/api/credentials",
            json={
                "name": "openai_key",
                "provider": "openai",
                "secret": "sk-test",
                "access": "shared",
            },
        ).json()
        scoped = api_client.post(
            "/api/credentials",
            json={
                "name": "slack_token",
                "provider": "slack",
                "secret": "xoxb",
                "access": "scoped",
                "workflow_id": workflow_id,
            },
        ).json()
        listed = await mcp.call("list_credentials")
        deleted = await mcp.call("delete_credential", {"credential_id": shared["id"]})
        missing = str(uuid4())
        not_found = await mcp.call_error(
            "delete_credential", {"credential_id": missing}
        )
        remaining = await mcp.call("list_credentials", {"workflow": workflow_id})

    assert {item["name"] for item in listed["credentials"]} == {
        "openai_key",
        "slack_token",
    }
    assert all("secret" not in item for item in listed["credentials"])
    assert deleted == {"status": "deleted", "credential_id": shared["id"]}
    assert f"/api/credentials/{missing} failed with HTTP 404" in not_found
    assert [item["id"] for item in remaining["credentials"]] == [scoped["id"]]


@pytest.mark.asyncio()
async def test_workspace_and_server_tools(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        memberships = await mcp.call("list_my_workspaces")
        active = await mcp.call("get_active_workspace")
        info = await mcp.call("get_server_info")

    assert memberships["memberships"][0]["slug"] == "default"
    assert active["slug"] == "default"
    assert "uploads_allowed" in info
