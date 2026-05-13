"""Tests for ChatKit workflow trigger endpoint."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import CredentialHealthStatus
from orcheo.models.workflow import WorkflowRun, WorkflowVersion
from orcheo.vault.oauth import (
    CredentialHealthError,
    CredentialHealthReport,
    CredentialHealthResult,
)
from orcheo_backend.app import trigger_chatkit_workflow
from orcheo_backend.app.authentication import AuthorizationPolicy
from orcheo_backend.app.authentication.context import RequestContext
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import chatkit as chatkit_router
from orcheo_backend.app.schemas.chatkit import ChatKitWorkflowTriggerRequest


def _health_error(workflow_id: UUID) -> CredentialHealthError:
    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[
            CredentialHealthResult(
                credential_id=uuid4(),
                name="Slack",
                provider="slack",
                status=CredentialHealthStatus.UNHEALTHY,
                last_checked_at=datetime.now(tz=UTC),
                failure_reason="expired",
            )
        ],
        checked_at=datetime.now(tz=UTC),
    )
    return CredentialHealthError(report)


def _authenticated_policy(subject: str = "tester") -> AuthorizationPolicy:
    """Return an authorization policy representing an authenticated identity."""

    context = RequestContext(subject=subject, identity_type="user")
    return AuthorizationPolicy(context)


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_creates_run() -> None:
    """ChatKit trigger creates a workflow run."""

    workflow_id = uuid4()
    run_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph={},
                created_by="system",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

        async def create_run(
            self, wf_id, workflow_version_id, triggered_by, input_payload
        ):
            return WorkflowRun(
                id=run_id,
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=input_payload,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = ChatKitWorkflowTriggerRequest(
        message="Hello",
        client_thread_id="thread-123",
        actor="user@example.com",
    )

    result = await trigger_chatkit_workflow(
        str(workflow_id),
        request,
        Repository(),
        _authenticated_policy(),
    )

    assert result.id == run_id
    assert result.triggered_by == "user@example.com"


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_missing_workflow() -> None:
    """ChatKit trigger raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            raise WorkflowNotFoundError("not found")

    request = ChatKitWorkflowTriggerRequest(
        message="Hello",
        client_thread_id="thread-123",
        actor="user@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        await trigger_chatkit_workflow(
            str(workflow_id),
            request,
            Repository(),
            _authenticated_policy(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_credential_health_error() -> None:
    """ChatKit trigger handles credential health errors."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph={},
                created_by="system",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

        async def create_run(
            self, wf_id, workflow_version_id, triggered_by, input_payload
        ):
            raise _health_error(wf_id)

    request = ChatKitWorkflowTriggerRequest(
        message="Hello",
        client_thread_id="thread-123",
        actor="user@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        await trigger_chatkit_workflow(
            str(workflow_id),
            request,
            Repository(),
            _authenticated_policy(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_handles_missing_run_workflow() -> None:
    """ChatKit trigger re-raises workflow errors from create_run."""

    workflow_id = uuid4()

    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=workflow_id,
        version=1,
        graph={},
        created_by="system",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            return version

        async def create_run(
            self, wf_id, workflow_version_id, triggered_by, input_payload
        ):
            raise WorkflowNotFoundError("workflow removed")

    request = ChatKitWorkflowTriggerRequest(
        message="Hello",
        client_thread_id="thread-123",
        actor="user@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        await trigger_chatkit_workflow(
            str(workflow_id),
            request,
            Repository(),
            _authenticated_policy(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_handles_missing_run_version() -> None:
    """ChatKit trigger re-raises version errors from create_run."""

    workflow_id = uuid4()

    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=workflow_id,
        version=1,
        graph={},
        created_by="system",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            return version

        async def create_run(
            self, wf_id, workflow_version_id, triggered_by, input_payload
        ):
            raise WorkflowVersionNotFoundError("version missing")

    request = ChatKitWorkflowTriggerRequest(
        message="Hello",
        client_thread_id="thread-123",
        actor="user@example.com",
    )

    with pytest.raises(HTTPException):
        await trigger_chatkit_workflow(
            str(workflow_id),
            request,
            Repository(),
            _authenticated_policy(),
        )


@pytest.mark.asyncio()
async def test_trigger_chatkit_workflow_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unauthenticated requests to the trigger endpoint are rejected."""

    workflow_id = uuid4()

    monkeypatch.setattr(
        chatkit_router,
        "load_auth_settings",
        lambda: SimpleNamespace(enforce=True),
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, wf_id):
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph={},
                created_by="system",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

        async def create_run(
            self, wf_id, workflow_version_id, triggered_by, input_payload
        ):
            return WorkflowRun(
                id=uuid4(),
                workflow_version_id=workflow_version_id,
                triggered_by=triggered_by,
                input_payload=input_payload,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = ChatKitWorkflowTriggerRequest(message="Hello")

    with pytest.raises(HTTPException) as exc_info:
        await trigger_chatkit_workflow(
            str(workflow_id),
            request,
            Repository(),
            AuthorizationPolicy(RequestContext.anonymous()),
        )

    assert exc_info.value.status_code == 401
