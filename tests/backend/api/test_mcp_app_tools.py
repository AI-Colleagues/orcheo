"""End-to-end tests for the MCP Apps: credential form and workflow diagram."""

from __future__ import annotations
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
)
from tests.backend.api.mcp_support import McpCaller, cron_script, mcp_session


SECRET = "sk-live-must-never-reach-the-model"  # noqa: S105 - test fixture


async def _open_form(mcp: McpCaller, **arguments: object) -> tuple[dict, str]:
    result = await mcp.call_result("open_credential_form", arguments)
    return result, result["_meta"][FORM_TOKEN_META_KEY]


@pytest.mark.asyncio()
async def test_tools_link_to_their_views(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        tools = {
            tool["name"]: tool
            for tool in (await mcp.rpc("tools/list")).json()["result"]["tools"]
        }

    assert tools["open_credential_form"]["_meta"]["ui"] == {
        "resourceUri": "ui://orcheo/credential-form"
    }
    assert tools["save_credential"]["_meta"]["ui"]["visibility"] == ["app"]
    assert tools["show_workflow_diagram"]["_meta"]["ui"] == {
        "resourceUri": "ui://orcheo/workflow-diagram"
    }
    assert "create_credential" not in tools
    assert "secret" not in tools["open_credential_form"]["inputSchema"]["properties"]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("uri", "csp"),
    [
        ("ui://orcheo/credential-form", None),
        (
            "ui://orcheo/workflow-diagram",
            {"resourceDomains": ["https://cdn.jsdelivr.net"]},
        ),
    ],
)
async def test_views_are_served_as_mcp_apps(
    api_client: TestClient, uri: str, csp: dict | None
) -> None:
    async with mcp_session(api_client.app) as mcp:
        listed = (await mcp.rpc("resources/list")).json()["result"]["resources"]
        read = (await mcp.rpc("resources/read", {"uri": uri})).json()["result"]

    content = read["contents"][0]
    assert uri in {resource["uri"] for resource in listed}
    assert content["mimeType"] == "text/html;profile=mcp-app"
    assert content["_meta"]["ui"].get("csp") == csp
    assert "ORCHEO_BRIDGE" not in content["text"]
    assert '"ui/initialize"' in content["text"]


@pytest.mark.asyncio()
async def test_credential_form_creates_without_exposing_the_secret(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        opened, token = await _open_form(mcp, name="openai_key", provider="openai")
        saved = await mcp.call_result(
            "save_credential",
            {
                "form_token": token,
                "name": "openai_key",
                "provider": "openai",
                "secret": SECRET,
            },
        )
        listed = await mcp.call("list_credentials")

    assert token not in json.dumps(opened["content"])
    assert opened["structuredContent"] == {"name": "openai_key", "provider": "openai"}
    assert "workspace 'default'" in opened["content"][0]["text"]
    assert SECRET not in json.dumps(saved)
    assert SECRET not in json.dumps(listed)
    credential_id = saved["structuredContent"]["id"]
    assert saved["structuredContent"]["access"] == "shared"
    revealed = api_client.get(f"/api/credentials/{credential_id}/secret").json()
    assert revealed["secret"] == SECRET


@pytest.mark.asyncio()
async def test_credential_form_updates_and_rotates(api_client: TestClient) -> None:
    workflow_id = api_client.post(
        "/api/workflows", json={"name": "Scoped", "actor": "t"}
    ).json()["id"]
    created = api_client.post(
        "/api/credentials",
        json={
            "name": "slack_token",
            "provider": "slack",
            "secret": "old",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    ).json()
    async with mcp_session(api_client.app) as mcp:
        opened, token = await _open_form(
            mcp, credential_id=created["id"], workflow=workflow_id
        )
        renamed = await mcp.call(
            "save_credential",
            {
                "form_token": token,
                "name": "slack_bot_token",
                "provider": "slack",
                "workflow": workflow_id,
            },
        )
        rotated = await mcp.call(
            "save_credential",
            {
                "form_token": token,
                "name": "slack_bot_token",
                "provider": "slack",
                "workflow": workflow_id,
                "secret": "new",
            },
        )

    assert opened["structuredContent"]["name"] == "slack_token"
    assert opened["structuredContent"]["access"] == "scoped"
    assert renamed["name"] == "slack_bot_token"
    assert rotated["id"] == created["id"]
    secret = api_client.get(
        f"/api/credentials/{created['id']}/secret",
        params={"workflow_id": workflow_id},
    ).json()["secret"]
    assert secret == "new"


@pytest.mark.asyncio()
async def test_credential_form_rejects_bad_requests(api_client: TestClient) -> None:
    missing_id = str(uuid4())
    async with mcp_session(api_client.app) as mcp:
        unknown = await mcp.call_error(
            "open_credential_form", {"credential_id": missing_id}
        )
        _, token = await _open_form(mcp)
        no_secret = await mcp.call_error(
            "save_credential",
            {"form_token": token, "name": "x", "provider": "y"},
        )
        forged = await mcp.call_error(
            "save_credential",
            {"form_token": f"{token}x", "name": "x", "provider": "y", "secret": "s"},
        )
        garbage = await mcp.call_error(
            "save_credential",
            {"form_token": "garbage", "name": "x", "provider": "y", "secret": "s"},
        )
        someone_else = issue_form_token(
            CredentialForm(subject="mallory", workspace="default", credential_id=None)
        )
        other_user = await mcp.call_error(
            "save_credential",
            {"form_token": someone_else, "name": "x", "provider": "y", "secret": "s"},
        )
        stale = issue_form_token(
            CredentialForm(
                subject="anonymous", workspace="default", credential_id=None
            ),
            now=0,
        )
        expired = await mcp.call_error(
            "save_credential",
            {"form_token": stale, "name": "x", "provider": "y", "secret": "s"},
        )

    assert f"Credential '{missing_id}' was not found." in unknown
    assert "Enter a secret" in no_secret
    assert "invalid" in forged
    assert "invalid" in garbage
    assert "different user" in other_user
    assert "expired" in expired


@pytest.mark.asyncio()
async def test_show_workflow_diagram(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        uploaded = await mcp.call(
            "upload_workflow",
            {"script": cron_script(), "name": "Diagram", "entrypoint": "build_graph"},
        )
        result = await mcp.call_result(
            "show_workflow_diagram", {"workflow": uploaded["workflow"]["id"]}
        )

    structured = result["structuredContent"]
    assert structured["name"] == "Diagram"
    assert structured["version"] == 1
    assert "cron" in structured["mermaid"]
    assert result["content"][0]["text"].startswith("Workflow 'Diagram' v1:\n```mermaid")


def test_form_tokens_use_the_jwt_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.app.mcp_server import form_tokens

    form = CredentialForm(subject="u", workspace=None, credential_id=None)
    process_token = issue_form_token(form)
    monkeypatch.setattr(
        form_tokens,
        "load_auth_settings",
        lambda: type("S", (), {"jwt_secret": "configured"})(),
    )
    configured_token = issue_form_token(form)

    assert form_tokens.verify_form_token(configured_token, subject="u") == form
    with pytest.raises(Exception, match="invalid"):
        form_tokens.verify_form_token(process_token, subject="u")


def test_form_tokens_fall_back_to_a_process_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.app.mcp_server import form_tokens

    monkeypatch.setattr(
        form_tokens,
        "load_auth_settings",
        lambda: type("S", (), {"jwt_secret": None})(),
    )
    form = CredentialForm(subject="u", workspace="w", credential_id="c")

    token = issue_form_token(form)

    assert form_tokens.verify_form_token(token, subject="u") == form
