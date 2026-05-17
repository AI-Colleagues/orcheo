"""Publish authentication tests for ChatKit router helper functions."""

from __future__ import annotations
from datetime import UTC, datetime
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
async def test_authenticate_publish_request_requires_oauth_when_flag_enabled() -> None:
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


@pytest.mark.asyncio()
async def test_authenticate_publish_request_accepts_oauth_session() -> None:
    request = make_chatkit_request(cookies={"orcheo_oauth_session": "abc"})
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

    result = await chatkit._authenticate_publish_request(
        request=request,
        workflow_id=workflow.id,
        now=datetime.now(tz=UTC),
        repository=repository,
    )
    assert result.subject == "abc"


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
