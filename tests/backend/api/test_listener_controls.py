"""API tests for listener health, controls, and metrics."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from orcheo.listeners import (
    ListenerHealthSnapshot,
    ListenerPlatform,
    ListenerSubscriptionStatus,
)
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.dependencies import (
    get_listener_runtime_store,
    get_repository,
)
from orcheo_backend.app.repository import InMemoryWorkflowRepository


async def _create_listener_workflow(
    repository: InMemoryWorkflowRepository,
) -> tuple[str, str]:
    workflow = await repository.create_workflow(
        name="Listener Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph={
            "nodes": [],
            "edges": [],
            "index": {
                "listeners": [
                    {
                        "node_name": "telegram_listener",
                        "platform": "telegram",
                        "token": "[[telegram_token]]",
                        "allowed_updates": ["message"],
                        "allowed_chat_types": ["private"],
                    }
                ]
            },
        },
        metadata={},
        notes=None,
        created_by="tester",
    )
    return str(workflow.id), str(version.id)


def test_listener_endpoints_report_health_and_allow_pause_resume(
    api_client: TestClient,
) -> None:
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()
    runtime_store.update(
        ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id="listener-runtime-1",
            status="healthy",
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=datetime.now(tz=UTC),
            consecutive_failures=0,
        )
    )

    list_response = api_client.get(f"/api/workflows/{workflow_id}/listeners")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["runtime_status"] == "healthy"
    assert payload[0]["status"] == "active"

    pause_response = api_client.post(
        f"/api/workflows/{workflow_id}/listeners/{subscription.id}/pause",
        json={"actor": "tester"},
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"

    resume_response = api_client.post(
        f"/api/workflows/{workflow_id}/listeners/{subscription.id}/resume",
        json={"actor": "tester"},
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "active"


def test_listener_metrics_endpoint_surfaces_alerts(api_client: TestClient) -> None:
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()
    runtime_store.update(
        ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id="listener-runtime-1",
            status="backoff",
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=datetime.now(tz=UTC) - timedelta(minutes=10),
            consecutive_failures=4,
            detail="gateway reconnecting",
        )
    )
    repository._listener_subscriptions[subscription.id].last_error = "dispatch failed"  # type: ignore[attr-defined]

    response = api_client.get(
        f"/api/workflows/{workflow_id}/listeners/metrics?stall_threshold_seconds=60"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_subscriptions"] == 1
    assert payload["reconnecting_runtimes"] == 1
    kinds = {alert["kind"] for alert in payload["alerts"]}
    assert "reconnect_loop" in kinds
    assert "dispatch_failure" in kinds


def test_list_workflow_listeners_no_health_snapshot(
    api_client: TestClient,
) -> None:
    """Covers line 44->50: snapshot is None → runtime_status defaults to 'unknown'.

    Also covers 110->119 (backoff condition False) and 119->96 (no last_error).
    """
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()

    response = api_client.get(f"/api/workflows/{workflow_id}/listeners")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["runtime_status"] == "unknown"

    # Also verify metrics with no alerts (110->119 and 119->96 False branches)
    metrics_response = api_client.get(
        f"/api/workflows/{workflow_id}/listeners/metrics?stall_threshold_seconds=300"
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["alerts"] == []


def test_list_workflow_listeners_error_status_overrides_runtime_status(
    api_client: TestClient,
) -> None:
    """Covers line 51: subscription.status==ERROR forces runtime_status to 'error'."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    asyncio.run(
        repository.update_listener_subscription_status(
            subscription.id,
            status=ListenerSubscriptionStatus.ERROR,
            actor="tester",
        )
    )
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()

    response = api_client.get(f"/api/workflows/{workflow_id}/listeners")
    assert response.status_code == 200
    items = response.json()
    assert items[0]["runtime_status"] == "error"


def test_list_workflow_listeners_workflow_not_found(
    api_client: TestClient,
) -> None:
    """Covers lines 144-145: get_workflow raises after handle resolves."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow = asyncio.run(
        repository.create_workflow(
            name="Phantom Workflow",
            handle="phantom-handle",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )
    # Remove from storage without rebuilding handle indexes so the handle still
    # resolves in resolve_workflow_ref but get_workflow raises WorkflowNotFoundError.
    del repository._workflows[workflow.id]  # type: ignore[attr-defined]

    response = api_client.get("/api/workflows/phantom-handle/listeners")
    assert response.status_code == 404


def test_get_metrics_workflow_not_found(
    api_client: TestClient,
) -> None:
    """Covers lines 173-174: get_workflow raises in get_workflow_listener_metrics."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow = asyncio.run(
        repository.create_workflow(
            name="Phantom Metrics Workflow",
            handle="phantom-metrics-handle",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )
    del repository._workflows[workflow.id]  # type: ignore[attr-defined]

    response = api_client.get("/api/workflows/phantom-metrics-handle/listeners/metrics")
    assert response.status_code == 404


def test_pause_listener_subscription_not_found_returns_404(
    api_client: TestClient,
) -> None:
    """Covers lines 77-78: get_listener_subscription raises WorkflowNotFoundError."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    fake_sub_id = uuid4()

    response = api_client.post(
        f"/api/workflows/{workflow_id}/listeners/{fake_sub_id}/pause",
        json={"actor": "tester"},
    )
    assert response.status_code == 404


def test_pause_listener_subscription_wrong_workflow_returns_404(
    api_client: TestClient,
) -> None:
    """Covers line 80: subscription belongs to a different workflow."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id_a, _ = asyncio.run(_create_listener_workflow(repository))
    workflow_id_b, _ = asyncio.run(_create_listener_workflow(repository))
    subscription_a = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id_a))
    )[0]

    response = api_client.post(
        f"/api/workflows/{workflow_id_b}/listeners/{subscription_a.id}/pause",
        json={"actor": "tester"},
    )
    assert response.status_code == 404


def test_metrics_stall_alert_triggered(
    api_client: TestClient,
) -> None:
    """Covers line 102: a stalled listener alert is appended when poll is old."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()
    runtime_store.update(
        ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id="listener-runtime-1",
            status="healthy",
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=datetime.now(tz=UTC) - timedelta(hours=1),
            consecutive_failures=0,
        )
    )

    response = api_client.get(
        f"/api/workflows/{workflow_id}/listeners/metrics?stall_threshold_seconds=60"
    )
    assert response.status_code == 200
    payload = response.json()
    kinds = {alert["kind"] for alert in payload["alerts"]}
    assert "stalled_listener" in kinds
    assert payload["stalled_listeners"] == 1


def test_metrics_breakdown_paused_and_healthy(
    api_client: TestClient,
) -> None:
    """Covers lines 198 (paused++), 200 (healthy++), and 201->190 (no error branch)."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    asyncio.run(
        repository.update_listener_subscription_status(
            subscription.id,
            status=ListenerSubscriptionStatus.PAUSED,
            actor="tester",
        )
    )
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()
    runtime_store.update(
        ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id="listener-runtime-1",
            status="healthy",
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=datetime.now(tz=UTC),
            consecutive_failures=0,
        )
    )

    response = api_client.get(f"/api/workflows/{workflow_id}/listeners/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["paused_subscriptions"] == 1
    assert payload["healthy_runtimes"] == 1
    by_platform = payload["by_platform"]
    assert len(by_platform) == 1
    breakdown = by_platform[0]
    assert breakdown["paused"] == 1
    assert breakdown["healthy"] == 1
    assert breakdown["errors"] == 0


def test_metrics_breakdown_error_counts(api_client: TestClient) -> None:
    """Ensure subscriptions in ERROR status increment the platform `errors` count."""
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    asyncio.run(
        repository.update_listener_subscription_status(
            subscription.id,
            status=ListenerSubscriptionStatus.ERROR,
            actor="tester",
            last_error="dispatch failure",
        )
    )
    runtime_store = get_listener_runtime_store()
    runtime_store.clear()
    runtime_store.update(
        ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id="listener-runtime-1",
            status="error",
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=datetime.now(tz=UTC),
            consecutive_failures=0,
        )
    )

    response = api_client.get(f"/api/workflows/{workflow_id}/listeners/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["error_subscriptions"] == 1
    breakdown = payload["by_platform"][0]
    assert breakdown["errors"] == 1


def test_metrics_breakdown_blocked_does_not_count_as_dispatch_failure(
    api_client: TestClient,
) -> None:
    repository = get_repository()
    assert isinstance(repository, InMemoryWorkflowRepository)
    workflow_id, _version_id = asyncio.run(_create_listener_workflow(repository))
    subscription = asyncio.run(
        repository.list_listener_subscriptions(workflow_id=UUID(workflow_id))
    )[0]
    asyncio.run(
        repository.update_listener_subscription_status(
            subscription.id,
            status=ListenerSubscriptionStatus.BLOCKED,
            actor="tester",
            last_error=(
                "Credential 'telegram_token' was not found in the configured vault"
            ),
        )
    )

    response = api_client.get(f"/api/workflows/{workflow_id}/listeners/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked_subscriptions"] == 1
    assert payload["dispatch_failures"] == 0
    assert payload["alerts"] == []
    breakdown = payload["by_platform"][0]
    assert breakdown["blocked"] == 1
    assert breakdown["errors"] == 0


# ---------------------------------------------------------------------------
# WeCom relay endpoint tests (lines 293-309, 324-348)
# ---------------------------------------------------------------------------


def test_relay_wecom_reply_plugin_not_installed_returns_501(
    api_client: TestClient,
) -> None:
    """_get_wecom_client returns 501 when WeCom plugin is not importable."""
    with patch.dict("sys.modules", {"orcheo_plugin_wecom_listener": None}):
        response = api_client.post(
            "/api/internal/listeners/wecom/reply",
            json={
                "subscription_id": "sub-123",
                "message": "hello",
                "raw_event": {},
            },
        )
    assert response.status_code == 501
    assert "WeCom listener plugin is not installed" in response.json()["detail"]


def test_relay_wecom_reply_client_not_found_returns_404(
    api_client: TestClient,
) -> None:
    """_get_wecom_client returns 404 when no active client for subscription."""
    mock_module = MagicMock()
    mock_module.get_wecom_client.return_value = None
    with patch.dict("sys.modules", {"orcheo_plugin_wecom_listener": mock_module}):
        response = api_client.post(
            "/api/internal/listeners/wecom/reply",
            json={
                "subscription_id": "sub-missing",
                "message": "hello",
                "raw_event": {},
            },
        )
    assert response.status_code == 404
    assert "sub-missing" in response.json()["detail"]


def test_relay_wecom_reply_build_body_plugin_not_installed_returns_501(
    api_client: TestClient,
) -> None:
    """relay_wecom_reply returns 501 when WS reply body builder is not importable."""
    mock_client = MagicMock()
    mock_loop = MagicMock()

    with patch(
        "orcheo_backend.app.routers.listeners._get_wecom_client",
        return_value=(mock_client, mock_loop),
    ):
        with patch.dict("sys.modules", {"orcheo_plugin_wecom_listener": None}):
            response = api_client.post(
                "/api/internal/listeners/wecom/reply",
                json={
                    "subscription_id": "sub-ok",
                    "message": "hello",
                    "raw_event": {},
                },
            )
    assert response.status_code == 501
    assert "WeCom listener plugin is not installed" in response.json()["detail"]


def test_relay_wecom_reply_success(
    api_client: TestClient,
) -> None:
    """relay_wecom_reply returns sent=True on success (lines 332-348)."""
    mock_get_module = MagicMock()
    mock_ws_client = MagicMock()
    mock_event_loop = MagicMock()
    mock_get_module.get_wecom_client.return_value = (mock_ws_client, mock_event_loop)
    mock_get_module.build_wecom_ws_reply_body.return_value = {
        "type": "text",
        "content": "hello",
    }

    with patch.dict("sys.modules", {"orcheo_plugin_wecom_listener": mock_get_module}):
        with patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
            mock_rcts.return_value = MagicMock()
            with patch(
                "asyncio.wrap_future", new_callable=AsyncMock, return_value=None
            ):
                response = api_client.post(
                    "/api/internal/listeners/wecom/reply",
                    json={
                        "subscription_id": "sub-ok",
                        "message": "hello",
                        "raw_event": {"key": "val"},
                    },
                )
    assert response.status_code == 200
    assert response.json() == {"sent": True}


def test_relay_wecom_reply_client_error_returns_502(
    api_client: TestClient,
) -> None:
    """relay_wecom_reply raises 502 when client.reply fails (lines 339-347)."""
    mock_module = MagicMock()
    mock_ws_client = MagicMock()
    mock_event_loop = MagicMock()
    mock_module.get_wecom_client.return_value = (mock_ws_client, mock_event_loop)
    mock_module.build_wecom_ws_reply_body.return_value = {"type": "text"}

    with patch.dict("sys.modules", {"orcheo_plugin_wecom_listener": mock_module}):
        with patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
            mock_rcts.return_value = MagicMock()
            with patch(
                "asyncio.wrap_future",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection lost"),
            ):
                response = api_client.post(
                    "/api/internal/listeners/wecom/reply",
                    json={
                        "subscription_id": "sub-fail",
                        "message": "hello",
                        "raw_event": {},
                    },
                )
    assert response.status_code == 502
    assert "connection lost" in response.json()["detail"]
