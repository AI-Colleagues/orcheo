from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from orcheo.models import Workflow
from orcheo.workspace import WorkspaceQuotas
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import WorkflowHandleConflictError
from orcheo_backend.app.routers import workflows
from orcheo_backend.app.schemas.workflows import WorkflowCreateRequest


_WORKSPACE = SimpleNamespace(
    workspace_id=uuid4(),
    workspace_slug="test-workspace",
    quotas=WorkspaceQuotas(),
)


@pytest.fixture(autouse=True)
def _patch_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_op_default_team(repository, workspace):  # noqa: ARG001
        return SimpleNamespace(id=uuid4())

    async def _no_op_quota(repository, workspace):  # noqa: ARG001
        return None

    monkeypatch.setattr(workflows, "ensure_default_team", _no_op_default_team)
    monkeypatch.setattr(workflows, "ensure_workspace_workflow_quota", _no_op_quota)


class _Repository:
    def __init__(self) -> None:
        self.default_team = SimpleNamespace(id=uuid4())

    async def ensure_default_team(
        self, *, workspace_id: str, name: str, slug: str
    ) -> SimpleNamespace:
        del workspace_id, name, slug
        return self.default_team

    async def create_workflow(self, **kwargs) -> Workflow:
        del kwargs
        return Workflow(
            id=uuid4(),
            name="unused",
            slug="unused",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )


@pytest.mark.asyncio
async def test_create_workflow_translates_handle_conflict_in_router() -> None:
    class Repository(_Repository):
        async def create_workflow(self, **kwargs) -> Workflow:
            del kwargs
            raise WorkflowHandleConflictError(
                "Workflow handle 'demo' is already in use."
            )

    request = WorkflowCreateRequest(name="Demo", handle="demo", actor="tester")

    with pytest.raises(HTTPException) as exc_info:
        await workflows.create_workflow(request, Repository(), _WORKSPACE)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.handle.conflict"


@pytest.mark.asyncio
async def test_create_workflow_translates_quota_error_in_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repository(_Repository):
        pass

    async def _raise_quota(*args, **kwargs) -> None:  # noqa: ARG001
        raise WorkspaceQuotaExceededError(
            "Workspace reached its workflow quota",
            code="workspace.quota.workflows",
            details={"limit": 1, "current": 1},
        )

    monkeypatch.setattr(workflows, "ensure_workspace_workflow_quota", _raise_quota)

    request = WorkflowCreateRequest(name="Demo", actor="tester")

    with pytest.raises(HTTPException) as exc_info:
        await workflows.create_workflow(request, Repository(), _WORKSPACE)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "workspace.quota.workflows"
