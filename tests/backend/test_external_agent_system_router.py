"""Tests for worker-scoped external agent system routes."""

from __future__ import annotations
from unittest.mock import Mock
import pytest
from orcheo.workspace import InMemoryWorkspaceRepository, WorkspaceService
from orcheo_backend.app import dependencies
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore
from orcheo_backend.app.routers import system as system_router
from orcheo_backend.app.schemas.system import ExternalAgentLoginSessionState
from orcheo_backend.app.workspace import reset_workspace_state, set_workspace_repository
from tests.backend.authentication_test_utils import create_test_client, reset_auth_state


_WORKSPACE_STATE: dict[str, str | None] = {"workspace_id": None}


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable auth and reset global state between tests."""
    yield from reset_auth_state(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_celery_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent Celery from connecting to Redis during router tests."""
    monkeypatch.setattr(system_router.celery_app, "send_task", Mock())
    yield


@pytest.fixture(autouse=True)
def _reset_workspace() -> None:
    """Reset workspace state between router tests."""
    yield
    reset_workspace_state()
    _WORKSPACE_STATE["workspace_id"] = None


def _default_workspace_id() -> str:
    current = _WORKSPACE_STATE["workspace_id"]
    if current is not None:
        return current
    repo = InMemoryWorkspaceRepository()
    set_workspace_repository(repo)
    workspace, _ = WorkspaceService(repo).create_workspace(
        slug="system", name="System Workspace", owner_user_id="alice"
    )
    _WORKSPACE_STATE["workspace_id"] = str(workspace.id)
    return _WORKSPACE_STATE["workspace_id"]


def _workspace_client(subject: str = "alice"):
    _default_workspace_id()
    client = create_test_client()

    async def _fake_auth() -> RequestContext:
        return RequestContext(
            subject=subject,
            identity_type="developer",
            scopes=frozenset({"workflows:read"}),
        )

    client.app.dependency_overrides[authenticate_request] = _fake_auth
    return client


@pytest.fixture
def runtime_store() -> ExternalAgentRuntimeStore:
    """Provide an isolated external agent runtime store for route tests."""
    original = dependencies.get_external_agent_runtime_store()
    store = ExternalAgentRuntimeStore()
    store._redis = None
    dependencies.set_external_agent_runtime_store(store)
    try:
        yield store
    finally:
        dependencies.set_external_agent_runtime_store(original)


def test_list_external_agents_returns_known_providers(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Listing external agents returns both worker-scoped provider entries."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    client = _workspace_client()
    response = client.get("/api/system/external-agents")

    assert response.status_code == 200
    payload = response.json()
    assert [item["provider"] for item in payload["providers"]] == [
        "claude_code",
        "codex",
        "gemini",
    ]
    assert all(item["workspace_id"] == workspace_id for item in payload["providers"])
    assert payload["providers"][0]["state"] == "unknown"


def test_refresh_external_agents_queues_worker_tasks(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Refreshing external agents queues a worker status probe for each provider."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    send_task = Mock()
    monkeypatch.setattr(system_router.celery_app, "send_task", send_task)

    client = _workspace_client()
    response = client.post("/api/system/external-agents/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert all(item["state"] == "checking" for item in payload["providers"])
    assert send_task.call_count == 3
    send_task.assert_any_call(
        "orcheo_backend.worker.tasks.refresh_external_agent_status",
        args=["claude_code", workspace_id],
    )
    send_task.assert_any_call(
        "orcheo_backend.worker.tasks.refresh_external_agent_status",
        args=["codex", workspace_id],
    )
    send_task.assert_any_call(
        "orcheo_backend.worker.tasks.refresh_external_agent_status",
        args=["gemini", workspace_id],
    )


def test_start_external_agent_login_creates_session_and_queues_worker_task(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Starting a login flow returns the session and marks the provider active."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    send_task = Mock()
    monkeypatch.setattr(system_router.celery_app, "send_task", send_task)

    client = _workspace_client()
    response = client.post("/api/system/external-agents/codex/login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "codex"
    assert payload["state"] == "pending"
    assert payload["session_id"]
    send_task.assert_called_once_with(
        "orcheo_backend.worker.tasks.start_external_agent_login",
        args=["codex", payload["session_id"], workspace_id],
    )

    status_response = client.get("/api/system/external-agents")
    assert status_response.status_code == 200
    statuses = {item["provider"]: item for item in status_response.json()["providers"]}
    assert statuses["codex"]["state"] == "authenticating"
    assert statuses["codex"]["active_session_id"] == payload["session_id"]


def test_start_external_agent_login_retries_with_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Retrying login should create a fresh worker session instead of reusing stale state."""  # noqa: E501
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")

    send_task = Mock()
    monkeypatch.setattr(system_router.celery_app, "send_task", send_task)

    client = _workspace_client()
    first = client.post("/api/system/external-agents/codex/login")
    second = client.post("/api/system/external-agents/codex/login")

    assert first.status_code == 200
    assert second.status_code == 200
    first_session = first.json()["session_id"]
    second_session = second.json()["session_id"]
    assert first_session != second_session
    assert send_task.call_count == 2


def test_disconnect_external_agent_queues_worker_task(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Disconnecting a provider should queue worker-side auth cleanup."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    send_task = Mock()
    monkeypatch.setattr(system_router.celery_app, "send_task", send_task)

    current = runtime_store.get_provider_status(
        system_router.ExternalAgentProviderName.CODEX,
        workspace_id=workspace_id,
    )
    runtime_store.save_provider_status(
        current.model_copy(
            update={
                "state": system_router.ExternalAgentProviderState.READY,
                "installed": True,
                "authenticated": True,
                "active_session_id": "session-1",
            }
        )
    )

    client = _workspace_client()
    response = client.post("/api/system/external-agents/codex/disconnect")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "codex"
    assert payload["state"] == "checking"
    assert payload["active_session_id"] == "session-1"
    send_task.assert_called_once_with(
        "orcheo_backend.worker.tasks.disconnect_external_agent",
        args=["codex", workspace_id],
    )


def test_missing_external_agent_login_session_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Requesting an unknown login session returns 404."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")

    client = _workspace_client()
    response = client.get("/api/system/external-agents/sessions/missing-session")

    assert response.status_code == 404


def test_get_external_agent_login_session_missing(monkeypatch, runtime_store):
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    client = _workspace_client()
    response = client.get("/api/system/external-agents/sessions/missing")

    assert response.status_code == 404


def test_get_external_agent_login_session_returns_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    client = _workspace_client()

    start = client.post("/api/system/external-agents/codex/login")
    session_id = start.json()["session_id"]

    response = client.get(f"/api/system/external-agents/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


def test_submit_external_agent_login_input_rejects_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()
    client = _workspace_client()
    start = client.post("/api/system/external-agents/claude_code/login")
    session_id = start.json()["session_id"]
    session = runtime_store.get_login_session(session_id, workspace_id=workspace_id)
    assert session is not None
    session.state = ExternalAgentLoginSessionState.AUTHENTICATED
    runtime_store.save_login_session(session)

    response = client.post(
        f"/api/system/external-agents/sessions/{session_id}/input",
        json={"input_text": "foo"},
    )

    assert response.status_code == 409


def test_submit_external_agent_login_input_rejects_empty(monkeypatch, runtime_store):
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    client = _workspace_client()
    start = client.post("/api/system/external-agents/claude_code/login")
    session_id = start.json()["session_id"]

    response = client.post(
        f"/api/system/external-agents/sessions/{session_id}/input",
        json={"input_text": "    "},
    )

    assert response.status_code == 400


def test_submit_external_agent_login_input_updates_session(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Submitting login input should queue it for the worker session."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    client = _workspace_client()
    start = client.post("/api/system/external-agents/claude_code/login")

    assert start.status_code == 200
    session_id = start.json()["session_id"]

    response = client.post(
        f"/api/system/external-agents/sessions/{session_id}/input",
        json={"input_text": "ABCD-1234"},
    )

    assert response.status_code == 200
    assert (
        runtime_store.get_login_input(session_id, workspace_id=workspace_id)
        == "ABCD-1234"
    )


def test_submit_external_agent_login_input_passes_bare_code_through(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Submitting a plain Claude code should pass it through without transformation."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    client = _workspace_client()
    start = client.post("/api/system/external-agents/claude_code/login")

    assert start.status_code == 200
    session_id = start.json()["session_id"]
    session = runtime_store.get_login_session(session_id, workspace_id=workspace_id)
    assert session is not None
    session.auth_url = (
        "https://claude.com/cai/oauth/authorize?code=true&state=test-state"
    )
    runtime_store.save_login_session(session)

    response = client.post(
        f"/api/system/external-agents/sessions/{session_id}/input",
        json={"input_text": "ABCD-1234"},
    )

    assert response.status_code == 200
    assert (
        runtime_store.get_login_input(
            session_id,
            workspace_id=workspace_id,
        )
        == "ABCD-1234"
    )


def test_submit_external_agent_login_input_passes_callback_url_through(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Submitting a Claude callback URL should pass it through for the CLI to parse."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    workspace_id = _default_workspace_id()

    client = _workspace_client()
    start = client.post("/api/system/external-agents/claude_code/login")

    assert start.status_code == 200
    session_id = start.json()["session_id"]

    callback_url = (
        "https://platform.claude.com/oauth/code/callback"
        "?code=ABCD-1234&state=test-state"
    )
    response = client.post(
        f"/api/system/external-agents/sessions/{session_id}/input",
        json={"input_text": callback_url},
    )

    assert response.status_code == 200
    assert (
        runtime_store.get_login_input(session_id, workspace_id=workspace_id)
        == callback_url
    )


def test_submit_external_agent_login_input_rejects_missing_session(
    monkeypatch: pytest.MonkeyPatch,
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    """Submitting login input to an unknown session returns 404."""
    assert runtime_store._redis is None
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")

    client = _workspace_client()
    response = client.post(
        "/api/system/external-agents/sessions/missing-session/input",
        json={"input_text": "ABCD-1234"},
    )

    assert response.status_code == 404
