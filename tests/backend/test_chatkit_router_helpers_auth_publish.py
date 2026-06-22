"""Publish authentication tests for ChatKit router helper functions."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, status
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)
from orcheo_backend.app.routers import chatkit
from tests.backend.chatkit_router_helpers_support import (
    make_chatkit_request,
)


pytestmark = pytest.mark.usefixtures("reset_chatkit_limiters")


class _MissingWorkflowPublishRepo:
    async def get_workflow(self, workflow_id: UUID) -> None:  # type: ignore[override]
        raise WorkflowNotFoundError(str(workflow_id))


@pytest.mark.asyncio()
async def test_authenticate_publish_request_missing_workflow() -> None:
    request = make_chatkit_request()
    workflow_id = uuid4()

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow_id,
            now=datetime.now(tz=UTC),
            repository=_MissingWorkflowPublishRepo(),
        )
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio()
async def test_authenticate_publish_request_requires_published_state() -> None:
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.not_published"


@pytest.mark.asyncio()
async def test_authenticate_publish_request_allows_public_workflow() -> None:
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish",
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

    result = await chatkit._authenticate_publish_request(
        request=request,
        workflow_id=workflow.id,
        now=datetime.now(tz=UTC),
        repository=repository,
    )
    assert result.actor == f"workflow:{workflow.id}"
    assert result.subject is None


@pytest.mark.asyncio()
async def test_authenticate_publish_request_requires_oauth_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chatkit,
        "load_auth_settings",
        lambda: SimpleNamespace(
            trusted_proxy_secret=None,
            trusted_proxy_ips=(),
            dev_login_enabled=False,
            dev_login_cookie_name=None,
            dev_login_workspace_ids=(),
        ),
    )
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.publish_workflow(
        workflow.id,
        require_login=True,
        actor="tester",
    )

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.oauth_required"


def _trusted_proxy_settings(secret: str = "s3cret") -> SimpleNamespace:
    return SimpleNamespace(
        trusted_proxy_secret=secret,
        trusted_proxy_ips=(),
        dev_login_enabled=False,
        dev_login_cookie_name=None,
        dev_login_workspace_ids=(),
    )


async def _publish_workspace_workflow(
    repository: InMemoryWorkflowRepository,
    *,
    workspace_id: str,
) -> UUID:
    workflow = await repository.create_workflow(
        name="Publish",
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
    return workflow.id


@pytest.mark.asyncio()
async def test_authenticate_publish_request_rejects_require_login_without_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chatkit, "load_auth_settings", _trusted_proxy_settings)
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.publish_workflow(
        workflow.id,
        require_login=True,
        actor="tester",
    )
    request = make_chatkit_request(
        headers={
            "X-Orcheo-Proxy-Secret": "s3cret",
            "X-Orcheo-OAuth-Subject": "alice@example.com",
            "X-Orcheo-OAuth-Workspaces": "ws-1",
        }
    )

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
    assert excinfo.value.detail["code"] == "chatkit.auth.workspace_required"


@pytest.mark.asyncio()
async def test_authenticate_publish_request_accepts_matching_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chatkit, "load_auth_settings", _trusted_proxy_settings)
    repository = InMemoryWorkflowRepository()
    workflow_id = await _publish_workspace_workflow(repository, workspace_id="ws-1")
    request = make_chatkit_request(
        headers={
            "X-Orcheo-Proxy-Secret": "s3cret",
            "X-Orcheo-OAuth-Subject": "alice@example.com",
            "X-Orcheo-OAuth-Workspaces": "ws-1",
        }
    )

    result = await chatkit._authenticate_publish_request(
        request=request,
        workflow_id=workflow_id,
        now=datetime.now(tz=UTC),
        repository=repository,
    )
    assert result.subject == "alice@example.com"


@pytest.mark.asyncio()
async def test_authenticate_publish_request_rejects_wrong_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chatkit, "load_auth_settings", _trusted_proxy_settings)
    repository = InMemoryWorkflowRepository()
    workflow_id = await _publish_workspace_workflow(repository, workspace_id="ws-1")
    request = make_chatkit_request(
        headers={
            "X-Orcheo-Proxy-Secret": "s3cret",
            "X-Orcheo-OAuth-Subject": "mallory@example.com",
            "X-Orcheo-OAuth-Workspaces": "ws-other",
        }
    )

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow_id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.workspace_mismatch"
    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio()
async def test_authenticate_publish_request_ignores_spoofed_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No proxy trust configured -> forwarded identity headers must be ignored.
    monkeypatch.setattr(
        chatkit,
        "load_auth_settings",
        lambda: _trusted_proxy_settings(secret=""),
    )
    repository = InMemoryWorkflowRepository()
    workflow_id = await _publish_workspace_workflow(repository, workspace_id="ws-1")
    request = make_chatkit_request(
        headers={
            "X-Orcheo-OAuth-Subject": "attacker@example.com",
            "X-Orcheo-OAuth-Workspaces": "ws-1",
        }
    )

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow_id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.oauth_required"


@pytest.mark.asyncio()
async def test_authenticate_publish_request_rejects_archived_workflow() -> None:
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish",
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
    await repository.archive_workflow(workflow.id, actor="tester")

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_publish_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
