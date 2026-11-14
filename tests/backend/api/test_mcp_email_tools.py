"""Exercise email tool registration and the private mailbox form over MCP."""

from __future__ import annotations
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.mcp_server.form_tokens import FORM_TOKEN_META_KEY
from orcheo_backend.app.routers.mcp import authenticate_mcp_request
from tests.backend.api.mcp_support import mcp_session


@pytest.mark.asyncio()
async def test_email_tools_publish_flat_schemas_and_private_actions(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        response = await mcp.rpc("tools/list")
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}

    save = tools["save_email_account"]
    assert save["_meta"]["ui"]["visibility"] == ["app"]
    assert set(save["inputSchema"]["required"]) == {
        "display_name",
        "email_address",
        "imap",
        "smtp",
        "form_token",
    }
    for name in ("send_email", "save_email_draft", "find_email_contacts"):
        schema = tools[name]["inputSchema"]
        assert "account_id" in schema["properties"]
        assert "account_id" not in schema["required"]
    assert tools["find_email_contacts"]["inputSchema"]["required"] == ["query"]
    # Only sending can be scheduled; the draft tool must not offer it.
    assert "send_at" in tools["send_email"]["inputSchema"]["properties"]
    assert "send_at" not in tools["save_email_draft"]["inputSchema"]["properties"]
    assert "idempotency_key" in tools["save_email_draft"]["inputSchema"]["properties"]


@pytest.mark.asyncio()
async def test_email_form_can_only_manage_workspace_mailboxes(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        api_client.app.dependency_overrides,
        authenticate_mcp_request,
        lambda: RequestContext(
            subject="anonymous",
            identity_type="user",
            claims={"client_id": "test-mcp"},
            scopes=frozenset({"email:read", "email:write"}),
        ),
    )
    response = api_client.post(
        "/api/credentials",
        json={
            "name": "other",
            "provider": "api",
            "secret": "other secret",
            "access": "shared",
        },
    )
    assert response.status_code == 201, response.text
    credential = response.json()
    vault = api_client.app.state.vault
    workspace_id = api_client.get("/api/workspaces/active").json()["workspace_id"]
    issued = vault.create_credential(
        name="issued-from-template",
        provider="orcheo-email",
        scopes=[],
        secret="{}",
        actor="test",
        workspace_id=workspace_id,
        template_id=uuid4(),
    )
    mailbox = {"display_name": "Example", "email_address": "me@example.org"}
    password = "test-mailbox-password"  # noqa: S105 - test fixture
    protocols = {
        "imap": {
            "host": "imap.example.org",
            "port": 993,
            "username": "me",
            "password": password,
        },
        "smtp": {
            "host": "smtp.example.org",
            "port": 465,
            "username": "me",
            "password": password,
        },
    }

    async with mcp_session(api_client.app) as mcp:
        opened = await mcp.call_result("open_email_settings")
        token = opened["_meta"][FORM_TOKEN_META_KEY]
        assert token not in json.dumps(opened["content"])
        for unrelated_id in (credential["id"], str(issued.id)):
            rejected = await mcp.call_error(
                "delete_email_account",
                {"form_token": token, "account_id": unrelated_id},
            )
            assert "Email account not found" in rejected
        payload = {"form_token": token, **mailbox, **protocols}
        saved = await mcp.call("save_email_account", payload)
        account_id = saved["account"]["account_id"]
        assert saved["account"]["is_default"] is True
        duplicate = await mcp.call_error("save_email_account", payload)
        assert "already in use" in duplicate
        account_ref = {"form_token": token, "account_id": account_id}
        settings = await mcp.call("get_email_account_settings", account_ref)
        assert settings["account"]["imap"]["host"] == "imap.example.org"
        assert password not in json.dumps([saved, settings])
        assert (
            api_client.get(f"/api/credentials/{account_id}/secret").status_code == 403
        )
        assert (
            api_client.patch(
                f"/api/credentials/{account_id}", json={"provider": "api"}
            ).status_code
            == 403
        )
        removed = await mcp.call("delete_email_account", account_ref)
        assert removed == {"deleted": True, "account_id": account_id}

    response = api_client.get(f"/api/credentials/{credential['id']}/secret")
    assert response.json()["secret"] == "other secret"


@pytest.mark.parametrize(
    "provider", ["orcheo-email", "orcheo-email-schedule", "orcheo-email-idempotency"]
)
def test_vault_routes_protect_existing_email_records(
    api_client: TestClient, provider: str
) -> None:
    workspace_id = api_client.get("/api/workspaces/active").json()["workspace_id"]
    entry = api_client.app.state.vault.create_credential(
        name="old-email-record",
        provider=provider,
        scopes=[],
        secret="private email data",
        actor="test",
        workspace_id=workspace_id,
    )
    url = f"/api/credentials/{entry.id}"
    assert api_client.get(f"{url}/secret").status_code == 403
    assert api_client.patch(url, json={"provider": "api"}).status_code == 403
    assert api_client.delete(url).status_code == 403
    assert str(entry.id) not in {
        item["id"] for item in api_client.get("/api/credentials").json()
    }
