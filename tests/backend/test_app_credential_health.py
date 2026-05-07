"""Tests for credential health endpoints and helpers."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, Request, status
from starlette.types import Message
from orcheo.models import CredentialHealthStatus
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo.vault.oauth import (
    CredentialHealthError,
    CredentialHealthReport,
    CredentialHealthResult,
)
from orcheo_backend.app import (
    _credential_service_ref,
    dispatch_cron_triggers,
    dispatch_manual_runs,
    get_workflow_credential_health,
    invoke_webhook_trigger,
    validate_workflow_credentials,
)
from orcheo_backend.app.repository import WorkflowNotFoundError
from orcheo_backend.app.routers import triggers as triggers_router
from orcheo_backend.app.schemas.credentials import CredentialValidationRequest


class _MissingWorkflowRepository:
    async def resolve_workflow_ref(
        self,
        workflow_ref,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ):
        del include_archived
        return (
            workflow_ref if isinstance(workflow_ref, UUID) else UUID(str(workflow_ref))
        )

    async def get_workflow(self, workflow_id):  # pragma: no cover - signature typing
        raise WorkflowNotFoundError("missing")


@pytest.mark.asyncio()
async def test_get_workflow_credential_health_handles_missing_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential health endpoint raises a 404 for unknown workflows."""

    monkeypatch.setitem(_credential_service_ref, "service", None)

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_credential_health(
            uuid4(),
            repository=_MissingWorkflowRepository(),
            service=None,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_credential_health_returns_unknown_response() -> None:
    """A missing cached report results in an UNKNOWN response payload."""

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived
            return UUID(str(workflow_ref))

        async def get_workflow(self, workflow_id):  # noqa: D401 - simple stub
            return object()

    class Service:
        def get_report(self, workflow_id):
            return None

    response = await get_workflow_credential_health(
        uuid4(), repository=Repository(), service=Service()
    )

    assert response.status is CredentialHealthStatus.UNKNOWN
    assert response.credentials == []


@pytest.mark.asyncio()
async def test_get_workflow_credential_health_requires_service() -> None:
    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived
            return UUID(str(workflow_ref))

        async def get_workflow(self, workflow_id):
            return object()

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_credential_health(
            uuid4(), repository=Repository(), service=None
        )

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio()
async def test_validate_workflow_credentials_reports_failures() -> None:
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

    class Service:
        async def ensure_workflow_health(self, workflow_id, *, actor=None):
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
            return report

    request = CredentialValidationRequest(actor="ops")
    with pytest.raises(HTTPException) as exc_info:
        await validate_workflow_credentials(
            workflow_id,
            request,
            repository=Repository(),
            service=Service(),
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail["failures"] == ["expired"]


@pytest.mark.asyncio()
async def test_validate_workflow_credentials_handles_missing_workflow() -> None:
    request = CredentialValidationRequest(actor="ops")

    with pytest.raises(HTTPException) as exc_info:
        await validate_workflow_credentials(
            uuid4(),
            request,
            repository=_MissingWorkflowRepository(),
            service=None,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


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


@pytest.mark.asyncio()
async def test_validate_workflow_credentials_requires_service() -> None:
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived
            return workflow_id

        async def get_workflow(self, workflow_id):
            return object()

    request = CredentialValidationRequest(actor="ops")
    with pytest.raises(HTTPException) as exc_info:
        await validate_workflow_credentials(
            workflow_id,
            request,
            repository=Repository(),
            service=None,
        )

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_wraps_health_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id):
            return object()

        async def handle_webhook_trigger(self, *args, **kwargs):
            raise _health_error(workflow_id)

    async def _fake_immediate_response(*_args, **_kwargs) -> tuple[None, bool]:
        return None, True

    monkeypatch.setattr(
        triggers_router,
        "_try_immediate_response",
        _fake_immediate_response,
    )

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)

    with pytest.raises(HTTPException) as exc_info:
        await invoke_webhook_trigger(
            str(workflow_id),
            request,
            repository=Repository(),
            vault=object(),
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio()
async def test_dispatch_cron_triggers_wraps_health_error() -> None:
    workflow_id = uuid4()

    class Repository:
        async def dispatch_due_cron_runs(self, now=None):
            raise _health_error(workflow_id)

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_cron_triggers(repository=Repository())

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio()
async def test_dispatch_manual_runs_wraps_health_error() -> None:
    workflow_id = uuid4()

    class Repository:
        async def dispatch_manual_runs(self, request):
            raise _health_error(workflow_id)

    manual_request = ManualDispatchRequest(
        workflow_id=workflow_id,
        actor="ops",
        runs=[ManualDispatchItem(input_payload={})],
    )

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_manual_runs(manual_request, repository=Repository())

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
