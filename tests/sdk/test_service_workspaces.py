"""Tests for the SDK workspace service helpers."""

from __future__ import annotations
from typing import Any
from orcheo_sdk.services import workspaces as workspace_service


class FakeClient:
    """Record HTTP calls made by the service helpers."""

    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]
        ] = []

    def post(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("post", url, json_body, params))
        return {"method": "post", "url": url, "json_body": json_body, "params": params}

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("get", url, None, params))
        return {"method": "get", "url": url, "params": params}

    def patch(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("patch", url, json_body, None))
        return {"method": "patch", "url": url, "json_body": json_body}

    def delete(self, url: str) -> dict[str, Any] | None:
        self.calls.append(("delete", url, None, None))
        return {"method": "delete", "url": url}


def test_workspace_service_helpers_construct_expected_requests() -> None:
    client = FakeClient()

    workspace_service.create_workspace_data(
        client,
        slug="acme",
        name="Acme",
        owner_user_id="user-1",
    )
    workspace_service.create_workspace_data(
        client,
        slug="globex",
        name="Globex",
        owner_user_id="user-2",
        quotas={"seats": 10},
    )
    workspace_service.list_workspaces_data(client)
    workspace_service.list_workspaces_data(client, include_inactive=True)
    workspace_service.deactivate_workspace_data(client, "ws-1")
    workspace_service.reactivate_workspace_data(client, "ws-1")
    workspace_service.delete_workspace_data(client, "ws-1")
    workspace_service.purge_deleted_workspaces_data(client)
    workspace_service.list_workspace_audit_events_data(client, "ws-1")
    workspace_service.invite_workspace_member_data(
        client,
        slug="acme",
        user_id="user-3",
        role="member",
    )
    workspace_service.list_my_workspaces_data(client)

    assert client.calls == [
        (
            "post",
            "/api/admin/workspaces",
            {
                "slug": "acme",
                "name": "Acme",
                "owner_user_id": "user-1",
            },
            None,
        ),
        (
            "post",
            "/api/admin/workspaces",
            {
                "slug": "globex",
                "name": "Globex",
                "owner_user_id": "user-2",
                "quotas": {"seats": 10},
            },
            None,
        ),
        ("get", "/api/admin/workspaces", None, None),
        ("get", "/api/admin/workspaces", None, {"include_inactive": "true"}),
        (
            "patch",
            "/api/admin/workspaces/ws-1/status",
            {"status": "suspended"},
            None,
        ),
        (
            "patch",
            "/api/admin/workspaces/ws-1/status",
            {"status": "active"},
            None,
        ),
        ("delete", "/api/admin/workspaces/ws-1", None, None),
        (
            "post",
            "/api/admin/workspaces/purge-deleted",
            None,
            {"retention_days": 30},
        ),
        (
            "get",
            "/api/admin/workspaces/ws-1/audit-events",
            None,
            {"limit": "100"},
        ),
        (
            "post",
            "/api/workspaces/acme/members",
            {"user_id": "user-3", "role": "member"},
            None,
        ),
        ("get", "/api/workspaces/me", None, None),
    ]


def test_workspace_service_helpers_support_custom_purge_and_limit() -> None:
    client = FakeClient()

    workspace_service.purge_deleted_workspaces_data(client, retention_days=45)
    workspace_service.list_workspace_audit_events_data(
        client,
        "ws-2",
        limit=5,
    )

    assert client.calls == [
        (
            "post",
            "/api/admin/workspaces/purge-deleted",
            None,
            {"retention_days": 45},
        ),
        (
            "get",
            "/api/admin/workspaces/ws-2/audit-events",
            None,
            {"limit": "5"},
        ),
    ]
