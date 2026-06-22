"""Tests covering the ChatKit authentication helper."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
import pytest
from starlette.requests import Request
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository.in_memory import InMemoryWorkflowRepository
from tests.backend.api.shared import backend_app


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chatkit",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope, _empty_receive)


@pytest.fixture(autouse=True)
def reset_rate_limiters() -> None:
    backend_app.routers.chatkit._reset_rate_limiters()
    rate_limiters = backend_app.routers.chatkit._get_rate_limiters()
    rate_limiters.ip.reset()
    rate_limiters.jwt.reset()
    rate_limiters.workflow.reset()
    rate_limiters.session.reset()


@pytest.mark.asyncio
async def test_authenticate_chatkit_invocation_with_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="JWT Workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    def mock_decode(_: str) -> dict[str, Any]:
        return {
            "sub": "alice",
            "chatkit": {"workflow_id": str(workflow.id), "token_id": "jwt-1"},
        }

    monkeypatch.setattr(backend_app.routers.chatkit, "_decode_chatkit_jwt", mock_decode)

    request = _make_request({"Authorization": "Bearer token"})
    result = await backend_app.routers.chatkit.authenticate_chatkit_invocation(
        request=request,
        payload={"workflow_id": str(workflow.id)},
        repository=repository,
    )

    assert result.auth_mode == "jwt"
    assert result.actor == "jwt:alice"
    assert result.subject == "alice"


@pytest.mark.asyncio
async def test_authenticate_chatkit_invocation_with_public_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish Workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    await repository.publish_workflow(
        workflow.id,
        require_login=False,
        actor="tester",
    )

    request = _make_request()
    result = await backend_app.routers.chatkit.authenticate_chatkit_invocation(
        request=request,
        payload={"workflow_id": str(workflow.id)},
        repository=repository,
    )

    assert result.auth_mode == "publish"
    assert result.subject is None
    assert result.actor == f"workflow:{workflow.id}"


@pytest.mark.asyncio
async def test_authenticate_chatkit_requires_session_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "test-workspace"
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Protected Workflow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
        workspace_id=workspace_id,
    )

    await repository.publish_workflow(
        workflow.id,
        require_login=True,
        actor="tester",
    )

    # Untrusted forwarded identity headers must be ignored -> still rejected.
    spoofed_request = _make_request({"X-Orcheo-OAuth-Subject": "bob"})
    with pytest.raises(backend_app.routers.chatkit.HTTPException) as exc:
        await backend_app.routers.chatkit.authenticate_chatkit_invocation(
            request=spoofed_request,
            payload={"workflow_id": str(workflow.id)},
            repository=repository,
        )

    assert exc.value.status_code == 401

    # Identity from a trusted proxy is accepted when the workspace is authorized.
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "load_auth_settings",
        lambda: SimpleNamespace(
            trusted_proxy_secret="s3cret",
            trusted_proxy_ips=(),
            dev_login_enabled=False,
            dev_login_cookie_name=None,
            dev_login_workspace_ids=(),
        ),
    )
    session_request = _make_request(
        {
            "X-Orcheo-Proxy-Secret": "s3cret",
            "X-Orcheo-OAuth-Subject": "bob",
            "X-Orcheo-OAuth-Workspaces": workspace_id,
        }
    )
    result = await backend_app.routers.chatkit.authenticate_chatkit_invocation(
        request=session_request,
        payload={"workflow_id": str(workflow.id)},
        repository=repository,
    )

    assert result.auth_mode == "publish"
    assert result.subject == "bob"
