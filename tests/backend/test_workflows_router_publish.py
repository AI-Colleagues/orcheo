"""Additional coverage for workflows publish router helpers."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowPublishStateError,
)
from orcheo_backend.app.routers import workflows
from orcheo_backend.app.schemas.workflows import (
    WorkflowPublishRequest,
    WorkflowPublishRevokeRequest,
)


class _MissingPublishRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        raise WorkflowNotFoundError(str(workflow_ref))

    async def publish_workflow(self, workflow_id: UUID, **kwargs: object) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


class _InvalidPublishRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))

    async def publish_workflow(self, workflow_id: UUID, **kwargs: object) -> Workflow:
        raise WorkflowPublishStateError("invalid state")


class _InvalidRevokeRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        return UUID(str(workflow_ref))

    async def revoke_publish(self, workflow_id: UUID, **kwargs: object) -> Workflow:
        raise WorkflowPublishStateError("invalid")


class _MissingRevokeRepo:
    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del include_archived
        raise WorkflowNotFoundError(str(workflow_ref))

    async def revoke_publish(self, workflow_id: UUID, **kwargs: object) -> Workflow:
        raise WorkflowNotFoundError(str(workflow_id))


class _RevokeRepo:
    def __init__(self) -> None:
        self.workflow = Workflow(name="Audit")

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del workflow_ref, include_archived
        return self.workflow.id

    async def revoke_publish(self, workflow_id: UUID, **kwargs: object) -> Workflow:
        return self.workflow


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


def test_publish_response_uses_message_helper() -> None:
    workflow = Workflow(name="Responder")
    workflow.share_url = "https://canvas.example/chat/wf-1"
    response = workflows._publish_response(workflow, message="ok")
    assert response.workflow is workflow
    assert response.message == "ok"
    assert response.share_url == "https://canvas.example/chat/wf-1"


@pytest.mark.asyncio()
async def test_publish_workflow_raises_not_found() -> None:
    request = WorkflowPublishRequest(actor="alice", require_login=False)

    with pytest.raises(HTTPException) as excinfo:
        await workflows.publish_workflow(
            str(uuid4()),
            request,
            _MissingPublishRepo(),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_publish_workflow_translates_state_errors() -> None:
    request = WorkflowPublishRequest(actor="alice", require_login=False)

    with pytest.raises(HTTPException) as excinfo:
        await workflows.publish_workflow(
            str(uuid4()),
            request,
            _InvalidPublishRepo(),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 409


@pytest.mark.asyncio()
async def test_revoke_publish_translates_state_errors() -> None:
    request = WorkflowPublishRevokeRequest(actor="alice")

    with pytest.raises(HTTPException) as excinfo:
        await workflows.revoke_workflow_publish(
            str(uuid4()),
            request,
            _InvalidRevokeRepo(),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 409


@pytest.mark.asyncio()
async def test_revoke_publish_not_found() -> None:
    request = WorkflowPublishRevokeRequest(actor="alice")

    with pytest.raises(HTTPException) as excinfo:
        await workflows.revoke_workflow_publish(
            str(uuid4()),
            request,
            _MissingRevokeRepo(),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_revoke_publish_logs_without_previous_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = WorkflowPublishRevokeRequest(actor="alice")
    repo = _RevokeRepo()
    captured: dict[str, str] = {}

    def _capture(message: str, *, extra: dict[str, str]) -> None:
        captured.update(extra)

    monkeypatch.setattr(workflows.logger, "info", _capture)

    result = await workflows.revoke_workflow_publish(
        str(repo.workflow.id),
        request,
        repo,
        _MOCK_WORKSPACE,
    )

    assert result is repo.workflow
    assert captured["workflow_id"] == str(repo.workflow.id)
