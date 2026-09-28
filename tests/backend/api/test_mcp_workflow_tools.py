"""End-to-end tests for the MCP workflow tools."""

from __future__ import annotations
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.backend.api.mcp_support import (
    SIMPLE_SCRIPT,
    McpCaller,
    cron_script,
    mcp_session,
)


async def _upload(mcp: McpCaller, **arguments: object) -> dict:
    return await mcp.call("upload_workflow", {"script": SIMPLE_SCRIPT, **arguments})


@pytest.mark.asyncio()
async def test_tools_are_listed_with_annotations(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        response = await mcp.rpc("tools/list")

    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert {"upload_workflow", "run_workflow", "list_components"} <= set(tools)
    assert tools["list_workflows"]["annotations"]["readOnlyHint"] is True
    assert tools["delete_workflow"]["annotations"]["destructiveHint"] is True
    assert "workspace" in tools["get_workflow"]["inputSchema"]["properties"]


@pytest.mark.asyncio()
async def test_upload_creates_named_workflow_and_strips_main_block(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        result = await _upload(
            mcp,
            name="Daily Digest",
            runnable_config={"configurable": {"topic": "ai"}},
        )
        workflow_id = result["workflow"]["id"]
        download = await mcp.call("download_workflow", {"workflow": workflow_id})

    assert result["created"] is True
    assert result["workflow"]["slug"] == "daily-digest"
    assert result["version"]["version"] == 1
    assert "graph" not in result["version"]
    assert result["credential_readiness"] is not None
    assert "cron_schedule" not in result
    assert "__main__" not in download["source"]
    assert download["runnable_config"]["configurable"] == {"topic": "ai"}


@pytest.mark.asyncio()
async def test_upload_by_handle_creates_then_updates(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        first = await _upload(mcp, workflow="Digest-Flow")
        unchanged = await _upload(mcp, workflow="digest-flow")
        renamed = await _upload(
            mcp, workflow="digest-flow", name="Digest", description="Daily"
        )
        by_id = await _upload(mcp, workflow=first["workflow"]["id"])

    assert first["created"] is True
    assert first["workflow"]["handle"] == "digest-flow"
    assert unchanged["created"] is False
    assert unchanged["version"]["version"] == 2
    assert renamed["workflow"]["name"] == "Digest"
    assert renamed["workflow"]["description"] == "Daily"
    assert by_id["version"]["version"] == 4


@pytest.mark.asyncio()
async def test_upload_recreates_archived_handle(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        first = await _upload(mcp, workflow="old-flow")
        await mcp.call("delete_workflow", {"workflow": "old-flow"})
        second = await _upload(mcp, workflow="old-flow")

    assert second["created"] is True
    assert second["workflow"]["id"] != first["workflow"]["id"]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Provide 'name'"),
        ({"workflow": "Not A Handle!"}, "Workflow handle must contain"),
        ({"workflow": str(uuid4())}, "HTTP 404"),
    ],
)
async def test_upload_rejects_invalid_targets(
    api_client: TestClient, arguments: dict, message: str
) -> None:
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error(
            "upload_workflow", {"script": SIMPLE_SCRIPT, **arguments}
        )

    assert message in error


@pytest.mark.asyncio()
async def test_upload_survives_readiness_failure(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.mcp_server import workflow_tools

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("vault offline")

    original_get = workflow_tools.InProcessApiClient.get

    async def _get(self, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
        if path.endswith("/credentials/readiness"):
            await _boom()
        return await original_get(self, path, **kwargs)

    monkeypatch.setattr(workflow_tools.InProcessApiClient, "get", _get)
    async with mcp_session(api_client.app) as mcp:
        result = await _upload(mcp, name="Resilient")

    assert result["credential_readiness"] is None


@pytest.mark.asyncio()
async def test_listing_and_inspection_tools(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        empty = await mcp.call(
            "upload_workflow", {"script": SIMPLE_SCRIPT, "name": "A"}
        )
        workflow_id = empty["workflow"]["id"]
        await _upload(mcp, workflow=workflow_id)
        await mcp.call("run_workflow", {"workflow": workflow_id})

        listing = await mcp.call("list_workflows")
        details = await mcp.call("get_workflow", {"workflow": workflow_id})
        pinned = await mcp.call("get_workflow", {"workflow": workflow_id, "version": 1})
        versions = await mcp.call("list_workflow_versions", {"workflow": workflow_id})
        old_source = await mcp.call(
            "download_workflow", {"workflow": workflow_id, "version": 1}
        )
        readiness = await mcp.call(
            "check_workflow_credentials", {"workflow": workflow_id}
        )
        missing_version = await mcp.call_error(
            "get_workflow", {"workflow": workflow_id, "version": 9}
        )

    listed = next(item for item in listing["workflows"] if item["id"] == workflow_id)
    assert "graph" not in listed["latest_version"]
    assert details["version"]["version"] == 2
    assert len(details["recent_runs"]) == 1
    assert pinned["version"]["version"] == 1
    assert [entry["version"] for entry in versions["versions"]] == [1, 2]
    assert old_source["version"] == 1
    assert readiness["workflow_id"] == workflow_id
    assert "HTTP 404" in missing_version


@pytest.mark.asyncio()
async def test_workflow_without_versions(api_client: TestClient) -> None:
    created = api_client.post("/api/workflows", json={"name": "Empty", "actor": "t"})
    workflow_id = created.json()["id"]
    async with mcp_session(api_client.app) as mcp:
        details = await mcp.call("get_workflow", {"workflow": workflow_id})
        download_error = await mcp.call_error(
            "download_workflow", {"workflow": workflow_id}
        )

    assert details["version"] is None
    assert "has no versions yet" in download_error


@pytest.mark.asyncio()
async def test_download_rejects_versions_without_source(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.mcp_server import workflow_tools

    async def _fetch(*_args: object) -> dict:
        return {"version": 3, "graph": {"format": "json"}}

    monkeypatch.setattr(workflow_tools, "fetch_version", _fetch)
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error("download_workflow", {"workflow": "anything"})

    assert "Version 3 has no Python source (format 'json')" in error


@pytest.mark.asyncio()
async def test_update_delete_and_publish(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        workflow_id = (await _upload(mcp, name="Chat"))["workflow"]["id"]
        no_change = await mcp.call_error("update_workflow", {"workflow": workflow_id})
        updated = await mcp.call(
            "update_workflow",
            {"workflow": workflow_id, "name": "Chatty", "handle": "chatty"},
        )
        published = await mcp.call(
            "publish_workflow", {"workflow": "chatty", "require_login": True}
        )
        revoked = await mcp.call("unpublish_workflow", {"workflow": "chatty"})
        archived = await mcp.call("delete_workflow", {"workflow": "chatty"})

    assert "Provide at least one" in no_change
    assert updated["name"] == "Chatty"
    assert published["workflow"]["is_public"] is True
    assert published["workflow"]["require_login"] is True
    assert revoked["is_public"] is False
    assert archived["is_archived"] is True


@pytest.mark.asyncio()
async def test_schedule_lifecycle(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        plain_id = (await _upload(mcp, name="Plain"))["workflow"]["id"]
        noop = await mcp.call("schedule_workflow", {"workflow": plain_id})

        cron = await mcp.call(
            "upload_workflow", {"script": cron_script(), "workflow": "cron-flow"}
        )
        scheduled = await mcp.call("schedule_workflow", {"workflow": "cron-flow"})
        same = await mcp.call(
            "upload_workflow", {"script": cron_script(), "workflow": "cron-flow"}
        )
        changed = await mcp.call(
            "upload_workflow",
            {"script": cron_script("0 9 * * *"), "workflow": "cron-flow"},
        )
        unscheduled = await mcp.call("unschedule_workflow", {"workflow": "cron-flow"})

    assert noop["status"] == "noop"
    assert "cron_schedule" not in cron
    assert scheduled["status"] == "scheduled"
    assert scheduled["config"]["expression"] == "*/5 * * * *"
    assert "cron_schedule" not in same
    assert changed["cron_schedule"]["status"] == "updated"
    assert changed["cron_schedule"]["config"]["expression"] == "0 9 * * *"
    assert unscheduled == {"status": "unscheduled"}


@pytest.mark.asyncio()
async def test_schedule_reports_invalid_cron_declarations(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo.triggers.cron_extraction import CronExtractionError
    from orcheo_backend.app.mcp_server import workflow_tools

    async with mcp_session(api_client.app) as mcp:
        await mcp.call(
            "upload_workflow", {"script": cron_script(), "workflow": "cron-flow"}
        )
        await mcp.call("schedule_workflow", {"workflow": "cron-flow"})

        def _invalid(_version: object) -> None:
            raise CronExtractionError("Workflow contains multiple cron triggers.")

        monkeypatch.setattr(workflow_tools, "extract_version_cron_config", _invalid)
        schedule_error = await mcp.call_error(
            "schedule_workflow", {"workflow": "cron-flow"}
        )
        upload = await mcp.call(
            "upload_workflow", {"script": cron_script(), "workflow": "cron-flow"}
        )

    assert "multiple cron triggers" in schedule_error
    assert upload["cron_schedule"] == {
        "status": "error",
        "message": "Workflow contains multiple cron triggers.",
    }


@pytest.mark.asyncio()
async def test_schedule_sync_propagates_unexpected_errors(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.mcp_server import workflow_tools
    from orcheo_backend.app.mcp_server.api_client import McpApiError

    original_get = workflow_tools.InProcessApiClient.get

    async def _get(self, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
        if path.endswith("/triggers/cron/config"):
            raise McpApiError("GET", path, 500, "scheduler unavailable")
        return await original_get(self, path, **kwargs)

    monkeypatch.setattr(workflow_tools.InProcessApiClient, "get", _get)
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error(
            "upload_workflow", {"script": SIMPLE_SCRIPT, "name": "X"}
        )

    assert "scheduler unavailable" in error


@pytest.mark.asyncio()
async def test_upload_propagates_unexpected_lookup_errors(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.mcp_server import workflow_tools
    from orcheo_backend.app.mcp_server.api_client import McpApiError

    async def _get(self, path: str, **_kwargs: object):  # type: ignore[no-untyped-def]
        raise McpApiError("GET", path, 403, {"message": "forbidden"})

    monkeypatch.setattr(workflow_tools.InProcessApiClient, "get", _get)
    async with mcp_session(api_client.app) as mcp:
        error = await mcp.call_error(
            "upload_workflow", {"script": SIMPLE_SCRIPT, "workflow": "some-flow"}
        )

    assert "HTTP 403: forbidden" in error


@pytest.mark.asyncio()
async def test_save_workflow_config(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        workflow_id = (await _upload(mcp, name="Configurable"))["workflow"]["id"]
        latest = await mcp.call(
            "save_workflow_config",
            {"workflow": workflow_id, "runnable_config": {"tags": ["a"]}},
        )
        pinned = await mcp.call(
            "save_workflow_config",
            {"workflow": workflow_id, "runnable_config": None, "version": 1},
        )

    assert latest["runnable_config"]["tags"] == ["a"]
    assert pinned["runnable_config"] is None
    assert "graph" not in pinned


@pytest.mark.asyncio()
async def test_listener_tools(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        workflow_id = (await _upload(mcp, name="Listeners"))["workflow"]["id"]
        listeners = await mcp.call("list_workflow_listeners", {"workflow": workflow_id})
        missing = str(uuid4())
        pause_error = await mcp.call_error(
            "pause_workflow_listener",
            {"workflow": workflow_id, "subscription_id": missing},
        )
        resume_error = await mcp.call_error(
            "resume_workflow_listener",
            {"workflow": workflow_id, "subscription_id": missing},
        )

    assert listeners == {"listeners": []}
    assert f"/listeners/{missing}/pause failed with HTTP 404" in pause_error
    assert f"/listeners/{missing}/resume failed with HTTP 404" in resume_error
