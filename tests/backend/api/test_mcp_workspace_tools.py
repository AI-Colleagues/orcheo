"""Exercise MCP workspace administration against the actual API routes."""

from __future__ import annotations
import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from orcheo.workspace import Role
from orcheo_backend.app.workspace.dependencies import (
    get_workspace_service,
    resolve_workspace_context,
)
from tests.backend.api.mcp_support import mcp_session


@pytest.mark.asyncio
async def test_workspace_lifecycle_and_admin_creation(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        own = await mcp.call("create_workspace", {"slug": "own", "name": "Own"})
        other = await mcp.call(
            "create_workspace",
            {
                "slug": "other",
                "name": "Other",
                "owner_user_id": "teammate",
                "quotas": {"max_workflows": 7},
            },
        )
        assert other["quotas"]["max_workflows"] == 7
        read = await mcp.call("get_workspace", {"workspace_id": own["id"]})
        assert read["slug"] == "own"
        listed = await mcp.call("list_workspaces")
        assert {"default", "own", "other"} <= {w["slug"] for w in listed["workspaces"]}
        for status in ("suspended", "active", "deleted"):
            changed = await mcp.call(
                "update_workspace_status", {"workspace_id": own["id"], "status": status}
            )
            assert changed["status"] == status
        active = await mcp.call("list_workspaces")
        assert "own" not in {w["slug"] for w in active["workspaces"]}
        inactive = await mcp.call("list_workspaces", {"include_inactive": True})
        assert "own" in {w["slug"] for w in inactive["workspaces"]}
        purged = await mcp.call("purge_deleted_workspaces", {"retention_days": 0})
        assert purged["purged"]
        assert "404" in await mcp.call_error(
            "get_workspace", {"workspace_id": own["id"]}
        )
        deleted = await mcp.call("delete_workspace", {"workspace_id": other["id"]})
        assert deleted["deleted"]


@pytest.mark.asyncio
async def test_members_invitations_and_audit_target_selected_workspace(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Use the real workspace resolver to exercise header and argument routing.
    api_client.app.dependency_overrides.pop(resolve_workspace_context)
    sender = Mock()
    monkeypatch.setattr(get_workspace_service(), "_email_sender", sender)
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call("create_workspace", {"slug": "team", "name": "Team"})
        member = await mcp.call(
            "add_workspace_member", {"workspace": "team", "user_id": "member"}
        )
        assert member["workspace_id"] == created["id"]
        updated = await mcp.call(
            "update_workspace_member_role",
            {"workspace": "team", "user_id": "member", "role": "viewer"},
        )
        assert updated["role"] == "viewer"
        members = await mcp.call("list_workspace_members", {"workspace": "team"})
        assert {m["user_id"] for m in members["members"]} == {"anonymous", "member"}
        default_members = await mcp.call(
            "list_workspace_members", {"workspace": "default"}
        )
        assert "member" not in {m["user_id"] for m in default_members["members"]}
        removed = await mcp.call(
            "remove_workspace_member", {"workspace": "team", "user_id": "member"}
        )
        assert removed["removed"]
        invitation = await mcp.call(
            "create_workspace_invitation",
            {"workspace": "team", "email": "new@example.com", "role": "editor"},
        )
        assert invitation["status"] == "pending"
        invitations = await mcp.call(
            "list_workspace_invitations", {"workspace": "team"}
        )
        assert len(invitations["invitations"]) == 1
        revoked = await mcp.call(
            "revoke_workspace_invitation",
            {"workspace": "team", "invitation_id": invitation["id"]},
        )
        assert revoked["status"] == "revoked"
        audit = await mcp.call(
            "list_workspace_audit_events",
            {"workspace": "team", "workspace_id": created["id"], "limit": 2},
        )
        assert 0 < len(audit["audit_events"]) <= 2
    sender.send_invitation.assert_called_once()
    email = sender.send_invitation.call_args.args[0]
    assert email.accept_url not in json.dumps([invitation, invitations, revoked, audit])


@pytest.mark.asyncio
async def test_workspace_roles_and_missing_membership_are_enforced(
    api_client: TestClient,
) -> None:
    context = api_client.app.dependency_overrides[resolve_workspace_context]()
    viewer = context.model_copy(update={"role": Role.VIEWER})
    api_client.app.dependency_overrides[resolve_workspace_context] = lambda: viewer
    async with mcp_session(api_client.app) as mcp:
        for tool, arguments in (
            ("list_workspaces", {}),
            ("get_workspace", {"workspace_id": str(context.workspace_id)}),
            ("add_workspace_member", {"user_id": "member"}),
            ("list_workspace_members", {}),
            ("create_workspace_invitation", {"email": "user@example.com"}),
            ("delete_workspace", {"workspace_id": str(context.workspace_id)}),
            (
                "create_workspace",
                {"slug": "assigned", "name": "Assigned", "owner_user_id": "other"},
            ),
        ):
            assert "403" in await mcp.call_error(tool, arguments)
        # Self-service creation does not require an existing administrator role.
        created = await mcp.call("create_workspace", {"slug": "self", "name": "Self"})
        assert created["slug"] == "self"
        api_client.app.dependency_overrides.pop(resolve_workspace_context)
        # Unknown workspaces are rejected by the same resolver as REST requests.
        assert "404" in await mcp.call_error(
            "list_workspace_members", {"workspace": "missing"}
        )
