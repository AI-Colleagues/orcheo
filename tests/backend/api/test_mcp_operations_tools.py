"""Exercise MCP operational tools against the backend routes and stores."""

from __future__ import annotations
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.dependencies import get_history_store
from orcheo_backend.app.mcp_server.form_tokens import (
    FORM_TOKEN_META_KEY,
    CredentialForm,
    issue_form_token,
)
from tests.backend.api.mcp_support import SIMPLE_SCRIPT, mcp_session


@pytest.mark.asyncio
async def test_workflow_config_metadata_and_version_diff(
    api_client: TestClient,
) -> None:
    async with mcp_session(api_client.app) as mcp:
        first = await mcp.call(
            "upload_workflow", {"name": "Config", "script": SIMPLE_SCRIPT}
        )
        wid = first["workflow"]["id"]
        await mcp.call(
            "save_workflow_config",
            {"workflow": wid, "runnable_config": {"configurable": {"model": "stored"}}},
        )
        await mcp.call("upload_workflow", {"workflow": wid, "script": SIMPLE_SCRIPT})
        run = await mcp.call(
            "run_workflow",
            {
                "workflow": wid,
                "version": 1,
                "runnable_config": {"configurable": {"model": "override"}},
            },
        )
        stored = await mcp.call("download_workflow", {"workflow": wid, "version": 1})
        updated = await mcp.call(
            "update_workflow",
            {
                "workflow": wid,
                "tags": ["demo"],
                "draft_access": "authenticated",
                "chatkit": {
                    "start_screen_prompts": [{"label": "Hello", "prompt": "Say hello"}],
                    "supported_models": [{"id": "test-model"}],
                },
            },
        )
        cleared = await mcp.call(
            "update_workflow",
            {"workflow": wid, "clear_chatkit_start_screen_prompts": True},
        )
        diff = await mcp.call(
            "diff_workflow_versions",
            {"workflow": wid, "base_version": 1, "target_version": 2},
        )
    assert run["workflow_version_id"] == first["version"]["id"]
    assert run["runnable_config"]["configurable"]["model"] == "override"
    assert stored["runnable_config"]["configurable"]["model"] == "stored"
    assert "demo" in updated["tags"]
    assert updated["chatkit"]["supported_models"][0]["id"] == "test-model"
    assert cleared["chatkit"]["start_screen_prompts"] is None
    assert cleared["chatkit"]["supported_models"][0]["id"] == "test-model"
    assert (diff["base_version"], diff["target_version"]) == (1, 2)


@pytest.mark.asyncio
async def test_execution_history_and_metrics(api_client: TestClient) -> None:
    history = api_client.app.dependency_overrides[get_history_store]()
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "History", "script": SIMPLE_SCRIPT}
        )
        wid = created["workflow"]["id"]
        eid = "streaming-execution-1"
        await history.start_run(
            workflow_id=wid,
            execution_id=eid,
            runnable_config={"configurable": {"x": 1}},
        )
        await history.append_step(eid, {"first": {"value": 1}})
        await history.append_step(eid, {"second": {"value": 2}})
        listed = await mcp.call("list_workflow_executions", {"workflow": wid})
        full = await mcp.call("get_execution_history", {"execution_id": eid})
        sliced = await mcp.call(
            "get_execution_history", {"execution_id": eid, "from_step": 1}
        )
        metrics = await mcp.call("get_workflow_listener_metrics", {"workflow": wid})
        missing = await mcp.call_error(
            "get_execution_history", {"execution_id": "missing"}
        )
    assert "steps" not in listed["executions"][0]
    assert len(full["steps"]) == 2
    assert sliced["steps"] == full["steps"][1:]
    assert full["runnable_config"]["configurable"] == {"x": 1}
    assert metrics["total_subscriptions"] == 0
    assert "HTTP 404" in missing


@pytest.mark.asyncio
async def test_credential_health_and_alerts(api_client: TestClient) -> None:
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "Health", "script": SIMPLE_SCRIPT}
        )
        wid = created["workflow"]["id"]
        validated = await mcp.call("validate_workflow_credentials", {"workflow": wid})
        cached = await mcp.call("get_workflow_credential_health", {"workflow": wid})
        alerts = await mcp.call(
            "list_credential_alerts", {"workflow": wid, "include_acknowledged": True}
        )
        missing = await mcp.call_error(
            "acknowledge_credential_alert", {"alert_id": str(uuid4()), "workflow": wid}
        )
    assert validated["status"] == cached["status"] == "healthy"
    assert alerts == {"alerts": []}
    assert "HTTP 404" in missing


@pytest.mark.asyncio
async def test_node_execution_and_server_diagnostics(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.routers import nodes, system
    from orcheo_backend.app.dependencies import get_plugin_installation_store
    from types import SimpleNamespace

    api_client.app.dependency_overrides[get_plugin_installation_store] = (
        lambda: SimpleNamespace(list_plugin_states=AsyncMock(return_value=[]))
    )

    node = AsyncMock(return_value={"node_results": {"preview": {"answer": 42}}})
    monkeypatch.setattr(nodes, "execute_node", node)
    monkeypatch.setattr(system, "inprocess_execution_enabled", lambda: True)
    async with mcp_session(api_client.app) as mcp:
        result = await mcp.call(
            "execute_node",
            {
                "node_config": {"type": "AgentNode", "name": "preview"},
                "inputs": {"question": "test"},
            },
        )
        ready = await mcp.call("get_server_readiness")
        features = await mcp.call("get_server_features")
        plugins = await mcp.call("list_server_plugins")
    assert result["result"] == {"answer": 42}
    assert node.call_args.kwargs["workspace_id"] is not None
    assert ready == {"status": "ok"}
    assert "hosted_apps_enabled" in features
    assert "plugins" in plugins


@pytest.mark.asyncio
async def test_webhook_form_and_secret_redaction(api_client: TestClient) -> None:
    secret = "webhook-secret-not-for-the-model"
    header_value = "header-secret-not-for-the-model"
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "Hook", "script": SIMPLE_SCRIPT}
        )
        wid = created["workflow"]["id"]
        opened = await mcp.call_result("open_webhook_form", {"workflow": wid})
        token = opened["_meta"][FORM_TOKEN_META_KEY]
        saved = await mcp.call(
            "save_webhook_config",
            {
                "form_token": token,
                "changes": {
                    "secret_header": "x-secret",
                    "shared_secret": secret,
                    "required_headers": {"x-required": header_value},
                },
            },
        )
        changed = await mcp.call(
            "configure_webhook",
            {"workflow": wid, "settings": {"allowed_methods": ["POST"]}},
        )
        read = await mcp.call("get_webhook_config", {"workflow": wid})
        reopened = await mcp.call_result("open_webhook_form", {"workflow": wid})
        error = await mcp.call_error(
            "save_webhook_config",
            {"form_token": token, "changes": {"hmac_secret": secret}},
        )
        wrong_audience = await mcp.call_error(
            "save_credential",
            {"form_token": token, "name": "bad", "provider": "test", "secret": "test"},
        )
        credential_token = issue_form_token(
            CredentialForm(subject="anonymous", workspace="default", credential_id=None)
        )
        reverse = await mcp.call_error(
            "save_webhook_config", {"form_token": credential_token, "changes": {}}
        )
        direct = await mcp.call_error(
            "configure_webhook",
            {"workflow": wid, "settings": {"shared_secret": secret}},
        )
        tools = (await mcp.rpc("tools/list")).json()["result"]["tools"]
        resource = (
            await mcp.rpc("resources/read", {"uri": "ui://orcheo/webhook-form"})
        ).json()["result"]
    serialized = json.dumps([saved, changed, read, reopened, error])
    assert secret not in serialized and header_value not in serialized
    assert read["has_shared_secret"] is True
    assert read["required_header_names"] == ["x-required"]
    assert read["allowed_methods"] == ["POST"]
    assert "HTTP 422" in error
    assert "cannot be used" in wrong_audience and "cannot be used" in reverse
    assert "Extra inputs" in direct
    assert token not in json.dumps(opened["content"])
    assert next(t for t in tools if t["name"] == "save_webhook_config")["_meta"]["ui"][
        "visibility"
    ] == ["app"]
    assert "ORCHEO_BRIDGE" not in resource["contents"][0]["text"]
    persisted = api_client.get(f"/api/workflows/{wid}/triggers/webhook/config").json()
    assert persisted["shared_secret"] == secret
    assert persisted["required_headers"] == {"x-required": header_value}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
async def test_evaluate_stored_version(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from orcheo_backend.app.routers import agentensor

    seen = {}

    async def evaluate(
        wid: str,
        graph: dict[str, Any],
        inputs: dict[str, Any],
        eid: str,
        sink: Any,
        **kwargs: Any,
    ) -> None:
        seen.update(
            workflow=wid, graph=graph, inputs=inputs, execution_id=eid, **kwargs
        )
        if outcome == "failure":
            raise RuntimeError("evaluation failed")
        if outcome == "timeout":
            await asyncio.sleep(2)
        await sink.send_json({"event": "evaluation_result", "payload": {"score": 1}})
        await sink.send_json({"status": "completed"})

    monkeypatch.setattr(agentensor, "execute_workflow_evaluation", evaluate)
    async with mcp_session(api_client.app) as mcp:
        first = await mcp.call(
            "upload_workflow",
            {
                "name": "Eval",
                "script": SIMPLE_SCRIPT,
                "runnable_config": {"configurable": {"version": 1}},
            },
        )
        wid = first["workflow"]["id"]
        await mcp.call("upload_workflow", {"workflow": wid, "script": SIMPLE_SCRIPT})
        args = {
            "workflow": wid,
            "version": 1,
            "evaluation": {"dataset": {"cases": [{"inputs": {"x": 1}}]}},
            "inputs": {"seed": 7},
            "timeout_seconds": 1,
        }
        if outcome == "success":
            result = await mcp.call("evaluate_workflow", args)
            assert result["status"] == "completed" and result["result"] == {"score": 1}
            assert result["execution_id"] == seen["execution_id"]
        else:
            error = await mcp.call_error("evaluate_workflow", args)
            assert ("HTTP 504" if outcome == "timeout" else "HTTP 422") in error
            assert seen["execution_id"] in error
    assert seen["workspace_id"] is not None
    assert seen["stored_runnable_config"]["configurable"]["version"] == 1
    assert seen["inputs"] == {"seed": 7}


@pytest.mark.asyncio
async def test_evaluation_rejects_custom_code_when_uploads_disabled(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.routers import agentensor

    async with mcp_session(api_client.app) as mcp:
        first = await mcp.call(
            "upload_workflow", {"name": "Guard", "script": SIMPLE_SCRIPT}
        )
        monkeypatch.setattr(agentensor, "uploads_allowed", lambda: False)
        error = await mcp.call_error(
            "evaluate_workflow",
            {
                "workflow": first["workflow"]["id"],
                "evaluation": {
                    "dataset": {"cases": [{"inputs": {}}]},
                    "evaluators": [{"id": "custom", "entrypoint": "custom:Evaluator"}],
                },
            },
        )
    assert "HTTP 403" in error


@pytest.mark.asyncio
async def test_candidate_onboard_and_update(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo_backend.app.routers import candidates
    from orcheo_backend.app.schemas.candidates import CandidateItem

    candidate = CandidateItem(
        id="test/candidate",
        handle="mcp-candidate",
        entrypoint="build_graph",
        name="Candidate",
        version="1.0.0",
        script=SIMPLE_SCRIPT.split("if __name__")[0],
    )
    monkeypatch.setattr(
        candidates, "get_candidates", AsyncMock(return_value=[candidate])
    )
    monkeypatch.setattr(
        candidates, "_fetch_candidate_by_id", AsyncMock(return_value=candidate)
    )
    async with mcp_session(api_client.app) as mcp:
        listed = await mcp.call("list_candidates")
        assert listed["candidates"][0]["id"] == candidate.id
        assert "script" not in listed["candidates"][0]
        installed = await mcp.call("onboard_candidate", {"candidate_id": candidate.id})
        candidate.version = "1.1.0"
        updated = await mcp.call(
            "update_candidate_workflow",
            {"workflow": installed["id"], "candidate_id": candidate.id},
        )
        assert updated["id"] == installed["id"]
        versions = await mcp.call(
            "list_workflow_versions", {"workflow": installed["id"]}
        )
        assert len(versions["versions"]) == 2


@pytest.mark.asyncio
async def test_checkpoint_tools(api_client: TestClient) -> None:
    from orcheo.agentensor.checkpoints import AgentensorCheckpoint
    from orcheo_backend.app.dependencies import get_checkpoint_store
    from types import SimpleNamespace

    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "Checkpoint", "script": SIMPLE_SCRIPT}
        )
        wid = created["workflow"]["id"]
        checkpoint = AgentensorCheckpoint(
            id="checkpoint-1",
            workflow_id=wid,
            config_version=1,
            runnable_config={},
            metrics={"score": 1},
            metadata={},
        )
        store = SimpleNamespace(
            list_checkpoints=AsyncMock(return_value=[checkpoint]),
            get_checkpoint=AsyncMock(return_value=checkpoint),
        )
        api_client.app.dependency_overrides[get_checkpoint_store] = lambda: store
        listed = await mcp.call(
            "list_agentensor_checkpoints", {"workflow": wid, "limit": 3}
        )
        detail = await mcp.call(
            "get_agentensor_checkpoint",
            {"workflow": wid, "checkpoint_id": "checkpoint-1"},
        )
        assert listed["checkpoints"][0] == detail
        assert store.list_checkpoints.call_args.kwargs["workspace_id"] is not None
        other = await mcp.call(
            "upload_workflow", {"name": "Other", "script": SIMPLE_SCRIPT}
        )
        denied = await mcp.call_error(
            "get_agentensor_checkpoint",
            {"workflow": other["workflow"]["id"], "checkpoint_id": "checkpoint-1"},
        )
        assert "HTTP 404" in denied


@pytest.mark.asyncio
async def test_validation_reports_provider_failures(api_client: TestClient) -> None:
    from types import SimpleNamespace
    from orcheo_backend.app.dependencies import get_credential_service

    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "Invalid token", "script": SIMPLE_SCRIPT}
        )
        failures = {"calendar": "Token expired"}
        service = SimpleNamespace(
            ensure_workflow_health=AsyncMock(
                return_value=SimpleNamespace(is_healthy=False, failures=failures)
            )
        )
        api_client.app.dependency_overrides[get_credential_service] = lambda: service
        report = await mcp.call(
            "validate_workflow_credentials", {"workflow": created["workflow"]["id"]}
        )
    assert report["status"] == "unhealthy"
    assert report["failures"] == failures


@pytest.mark.asyncio
async def test_new_workflow_tools_enforce_workspace_boundary(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orcheo.workspace import WorkspaceContext, Role
    from orcheo_backend.app.workspace.dependencies import resolve_workspace_context
    from orcheo_backend.app.routers import agentensor

    executor = AsyncMock()
    monkeypatch.setattr(agentensor, "execute_workflow_evaluation", executor)
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow", {"name": "Private", "script": SIMPLE_SCRIPT}
        )
        wid = created["workflow"]["id"]
        other = WorkspaceContext(
            workspace_id=uuid4(),
            workspace_slug="other",
            user_id="anonymous",
            role=Role.OWNER,
        )
        api_client.app.dependency_overrides[resolve_workspace_context] = lambda: other
        for name, args in [
            ("get_webhook_config", {}),
            ("configure_webhook", {"settings": {"allowed_methods": ["POST"]}}),
            ("list_workflow_executions", {}),
            ("get_workflow_credential_health", {}),
            ("get_workflow_listener_metrics", {}),
            (
                "evaluate_workflow",
                {"evaluation": {"dataset": {"cases": [{"inputs": {}}]}}},
            ),
        ]:
            error = await mcp.call_error(name, {"workflow": wid, **args})
            assert "HTTP 404" in error, (name, error)
    executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluation_executes_and_records_history(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evaluate a real graph with in-memory stores and no external providers."""
    from contextlib import asynccontextmanager
    from importlib import import_module

    backend = import_module("orcheo_backend.app")
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore

    @asynccontextmanager
    async def checkpointer(_settings: Any) -> AsyncIterator[MemorySaver]:
        yield MemorySaver()

    @asynccontextmanager
    async def graph_store(_settings: Any) -> AsyncIterator[InMemoryStore]:
        yield InMemoryStore()

    monkeypatch.setattr(backend, "create_checkpointer", checkpointer)
    monkeypatch.setattr(backend, "create_graph_store", graph_store)
    async with mcp_session(api_client.app) as mcp:
        created = await mcp.call(
            "upload_workflow",
            {
                "name": "Real evaluation",
                "script": SIMPLE_SCRIPT,
                "entrypoint": "build_graph",
            },
        )
        result = await mcp.call(
            "evaluate_workflow",
            {
                "workflow": created["workflow"]["id"],
                "evaluation": {"dataset": {"cases": [{"inputs": {"topic": "test"}}]}},
            },
        )
        history = await mcp.call(
            "get_execution_history", {"execution_id": result["execution_id"]}
        )
    assert result["status"] == "completed"
    assert result["result"] is not None
    assert history["status"] == "completed"
    assert any(
        step["payload"].get("event") == "evaluation_result" for step in history["steps"]
    )
