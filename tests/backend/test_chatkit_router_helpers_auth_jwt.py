"""Authentication tests for ChatKit router helper JWT workflows."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, status
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)
from orcheo_backend.app.routers import chatkit
from tests.backend.chatkit_router_helpers_support import (
    make_chatkit_request,
)


pytestmark = pytest.mark.usefixtures("reset_chatkit_limiters")


class _MissingWorkflowRepo:
    async def get_workflow(self, workflow_id: UUID) -> None:  # type: ignore[override]
        raise WorkflowNotFoundError(str(workflow_id))


@pytest.mark.asyncio()
async def test_authenticate_chatkit_invocation_requires_workflow_id() -> None:
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()

    with pytest.raises(HTTPException) as excinfo:
        await chatkit.authenticate_chatkit_invocation(
            request=request,
            payload={},
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.workflow_id_missing"


@pytest.mark.asyncio()
async def test_authenticate_chatkit_invocation_validates_workflow_id_format() -> None:
    request = make_chatkit_request()
    repository = InMemoryWorkflowRepository()

    with pytest.raises(HTTPException) as excinfo:
        await chatkit.authenticate_chatkit_invocation(
            request=request,
            payload={"workflow_id": "not-a-uuid"},
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.workflow_id_invalid"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_requires_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    monkeypatch.setattr(chatkit, "_decode_chatkit_jwt", lambda token: {})  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.invalid_jwt_claims"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_validates_workflow_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {"chatkit": {"workflow_id": "bad"}},
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.invalid_jwt_claims"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_detects_workflow_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    claimed_id = uuid4()
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {"chatkit": {"workflow_id": str(claimed_id)}},
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.workflow_mismatch"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_uses_verified_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {
            "chatkit": {
                "workspace_id": "workspace-a",
                "workspace_ids": ["workspace-a", "workspace-b"],
            }
        },
    )  # type: ignore[attr-defined]

    result = await chatkit._authenticate_jwt_request(
        request=request,
        workflow_id=workflow.id,
        now=datetime.now(tz=UTC),
        repository=repository,
    )

    assert result.workspace_id == "workspace-a"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_requires_selected_workspace_for_unscoped_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {
            "chatkit": {
                "workspace_ids": ["workspace-a", "workspace-b"],
            }
        },
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.workspace_required"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_requires_authorized_workflow_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
        workspace_id="workspace-b",
    )
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {
            "chatkit": {
                "workspace_id": "workspace-a",
                "workspace_ids": ["workspace-a"],
            }
        },
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.workspace_mismatch"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_rejects_unauthorized_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Claims",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {
            "chatkit": {
                "workspace_id": "workspace-a",
                "workspace_ids": ["workspace-b"],
            }
        },
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.detail["code"] == "chatkit.auth.workspace_mismatch"


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    workflow_id = uuid4()

    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {"chatkit": {"token_id": "jwt-1"}, "sub": "alice"},
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow_id,
            now=datetime.now(tz=UTC),
            repository=_MissingWorkflowRepo(),
        )
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio()
async def test_authenticate_jwt_request_rejects_archived_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_chatkit_request(headers={"Authorization": "Bearer token"})
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Archived",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.archive_workflow(workflow.id, actor="tester")

    monkeypatch.setattr(
        chatkit,
        "_decode_chatkit_jwt",
        lambda token: {"chatkit": {"token_id": "jwt-1"}, "sub": "alice"},
    )  # type: ignore[attr-defined]

    with pytest.raises(HTTPException) as excinfo:
        await chatkit._authenticate_jwt_request(
            request=request,
            workflow_id=workflow.id,
            now=datetime.now(tz=UTC),
            repository=repository,
        )
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
