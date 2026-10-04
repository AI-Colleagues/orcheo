"""Verify the private service token flow and model-visible secret boundaries."""

from __future__ import annotations
import json
from dataclasses import replace
from importlib import import_module
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.authentication import ServiceTokenManager
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    issue_form_token,
    verify_form_token,
)
from orcheo_backend.app.mcp_server.service_token_tools import (
    SERVICE_TOKEN_SECRET_META_KEY,
)
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context
from orcheo_backend.app.service_token_repository import InMemoryServiceTokenRepository
from tests.backend.api.mcp_support import mcp_session


@pytest.fixture(autouse=True)
def token_manager(monkeypatch: pytest.MonkeyPatch) -> ServiceTokenManager:
    manager = ServiceTokenManager(InMemoryServiceTokenRepository())
    monkeypatch.setattr(
        import_module("orcheo_backend.app.service_token_endpoints"),
        "get_service_token_manager",
        lambda: manager,
    )
    return manager


@pytest.mark.asyncio
async def test_service_token_app_metadata_and_resource(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        listing = (await mcp.rpc("tools/list")).json()["result"]["tools"]
        tools = {tool["name"]: tool for tool in listing}
        for name in ("create_service_token", "revoke_service_token"):
            assert tools[name]["_meta"]["ui"]["visibility"] == ["app"]
            assert "form_token" in tools[name]["inputSchema"]["required"]
        assert (
            "secret"
            not in tools["open_service_token_form"]["inputSchema"]["properties"]
        )
        assert (
            "token_id" not in tools["revoke_service_token"]["inputSchema"]["properties"]
        )
        resource = (
            await mcp.rpc("resources/read", {"uri": "ui://orcheo/service-token-form"})
        ).json()["result"]
        assert resource["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
        assert "ORCHEO_BRIDGE" not in resource["contents"][0]["text"]


@pytest.mark.asyncio
async def test_create_read_revoke_never_expose_secrets_to_model(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        opened = await mcp.call_result(
            "open_service_token_form",
            {"name": "Automation", "scopes": ["workflows:read"]},
        )
        form_token = opened["_meta"][FORM_TOKEN_META_KEY]
        assert form_token not in json.dumps(
            {k: v for k, v in opened.items() if k != "_meta"}
        )
        created = await mcp.call_result(
            "create_service_token",
            {
                "form_token": form_token,
                "name": "Automation",
                "scopes": ["workflows:read"],
                "expires_in_seconds": 3600,
            },
        )
        secret = created["_meta"][SERVICE_TOKEN_SECRET_META_KEY]
        token_id = created["structuredContent"]["identifier"]
        assert created["structuredContent"]["expires_at"] is not None
        assert secret not in json.dumps(
            {k: v for k, v in created.items() if k != "_meta"}
        )
        assert "secret_preview" not in created["structuredContent"]
        read = await mcp.call("get_service_token", {"token_id": token_id})
        listed = await mcp.call("list_service_tokens")
        assert read["identifier"] == token_id
        assert token_id in {t["identifier"] for t in listed["tokens"]}
        assert secret not in json.dumps([read, listed])
        assert "secret" not in read and "secret_preview" not in read
        revoke_form = await mcp.call_result(
            "open_service_token_form", {"action": "revoke", "token_id": token_id}
        )
        revoked = await mcp.call_result(
            "revoke_service_token",
            {
                "form_token": revoke_form["_meta"][FORM_TOKEN_META_KEY],
                "reason": "Replaced",
            },
        )
        assert revoked["structuredContent"] == {"identifier": token_id, "revoked": True}
        read = await mcp.call("get_service_token", {"token_id": token_id})
        assert read["revocation_reason"] == "Replaced"
        assert read["revoked_at"] is not None
        assert secret not in json.dumps([revoke_form, revoked, read])


@pytest.mark.asyncio
async def test_form_tokens_enforce_purpose_caller_workspace_and_expiration(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        opened = await mcp.call_result("open_service_token_form")
        token = opened["_meta"][FORM_TOKEN_META_KEY]
        form = verify_form_token(
            token, subject="anonymous", purpose="service_token_create"
        )
        cases = [
            (f"{token}x", "invalid"),
            (issue_form_token(replace(form, subject="other")), "different user"),
            (issue_form_token(replace(form, purpose="credential")), "operation"),
            (issue_form_token(replace(form, purpose="webhook")), "operation"),
            (
                issue_form_token(replace(form, client_id="other-client")),
                "different application",
            ),
            (
                issue_form_token(replace(form, workspace_id=str(uuid4()))),
                "workspace has changed",
            ),
            (issue_form_token(form, now=0), "expired"),
        ]
        for invalid, message in cases:
            error = await mcp.call_error(
                "create_service_token", {"form_token": invalid, "scopes": []}
            )
            assert message in error
        assert "operation" in await mcp.call_error(
            "revoke_service_token", {"form_token": token, "reason": "wrong action"}
        )
        assert "required" in await mcp.call_error(
            "create_service_token", {"scopes": []}
        )
        assert "token_id" in await mcp.call_error(
            "open_service_token_form", {"action": "revoke"}
        )
        assert "token_id" in await mcp.call_error(
            "open_service_token_form", {"token_id": "a"}
        )
        assert "404" in await mcp.call_error(
            "open_service_token_form", {"action": "revoke", "token_id": "missing"}
        )
        assert "permissions" in await mcp.call_error(
            "open_service_token_form", {"scopes": ["ungranted"]}
        )
        assert "403" in await mcp.call_error(
            "create_service_token", {"form_token": token, "scopes": ["ungranted"]}
        )
        assert "60" in await mcp.call_error(
            "create_service_token",
            {"form_token": token, "scopes": [], "expires_in_seconds": 1},
        )


@pytest.mark.asyncio
async def test_tokens_and_forms_stay_in_their_workspace(api_client: TestClient) -> None:
    api_client.app.dependency_overrides.pop(resolve_workspace_context)
    async with mcp_session(api_client.app) as mcp:
        team = await mcp.call("create_workspace", {"slug": "tokens", "name": "Tokens"})
        opened = await mcp.call_result(
            "open_service_token_form", {"workspace": "tokens"}
        )
        # Changing the request header cannot change the signed form's workspace.
        mcp.client.headers["X-Orcheo-Workspace"] = "default"
        created = await mcp.call_result(
            "create_service_token",
            {"form_token": opened["_meta"][FORM_TOKEN_META_KEY], "scopes": []},
        )
        token_id = created["structuredContent"]["identifier"]
        assert created["structuredContent"]["workspace_ids"] == [team["id"]]
        assert "404" in await mcp.call_error(
            "get_service_token", {"token_id": token_id}
        )
        assert token_id not in {
            t["identifier"] for t in (await mcp.call("list_service_tokens"))["tokens"]
        }
        assert "404" in await mcp.call_error(
            "open_service_token_form", {"action": "revoke", "token_id": token_id}
        )
        read = await mcp.call(
            "get_service_token", {"workspace": "tokens", "token_id": token_id}
        )
        assert read["identifier"] == token_id
        opened_revoke = await mcp.call_result(
            "open_service_token_form",
            {
                "workspace": "tokens",
                "action": "revoke",
                "token_id": token_id,
            },
        )
        assert "reason" in await mcp.call_error(
            "revoke_service_token",
            {
                "form_token": opened_revoke["_meta"][FORM_TOKEN_META_KEY],
                "reason": "  ",
            },
        )
        await mcp.call(
            "revoke_service_token",
            {
                "form_token": opened_revoke["_meta"][FORM_TOKEN_META_KEY],
                "reason": "Finished",
            },
        )
