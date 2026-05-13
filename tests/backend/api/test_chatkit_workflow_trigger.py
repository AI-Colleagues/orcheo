from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from orcheo.models import CredentialHealthStatus
from orcheo.vault.oauth import CredentialHealthReport, CredentialHealthResult
from .shared import backend_app, create_workflow_with_version


def test_chatkit_workflow_trigger_dispatches_run(api_client: TestClient) -> None:
    """Client tool triggers create workflow runs with ChatKit metadata."""

    workflow_id, workflow_version_id = create_workflow_with_version(api_client)

    response = api_client.post(
        f"/api/chatkit/workflows/{workflow_id}/trigger",
        json={
            "message": "Launch QA pipeline",
            "actor": "canvas-user",
            "client_thread_id": "thread-123",
            "metadata": {"priority": "high"},
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    payload = response.json()
    assert payload["workflow_version_id"] == workflow_version_id
    assert payload["triggered_by"] == "canvas-user"
    assert payload["input_payload"]["source"] == "chatkit"
    assert payload["input_payload"]["message"] == "Launch QA pipeline"
    assert payload["input_payload"]["client_thread_id"] == "thread-123"
    assert payload["input_payload"]["metadata"]["priority"] == "high"


def test_chatkit_workflow_trigger_requires_existing_workflow(
    api_client: TestClient,
) -> None:
    """Triggering a missing workflow returns a 404."""

    response = api_client.post(
        f"/api/chatkit/workflows/{uuid4()}/trigger",
        json={"message": "Unknown workflow"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_chatkit_workflow_trigger_requires_version(api_client: TestClient) -> None:
    """Workflows without versions return a not found response."""

    workflow_response = api_client.post(
        "/api/workflows", json={"name": "No Version", "actor": "tester"}
    )
    workflow_id = workflow_response.json()["id"]

    response = api_client.post(
        f"/api/chatkit/workflows/{workflow_id}/trigger",
        json={"message": "Should fail"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_chatkit_workflow_trigger_surfaces_credential_health_error(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient
) -> None:
    """Credential health failures are mapped to a 422 response."""

    workflow_id, _ = create_workflow_with_version(api_client)
    repository = api_client.app.dependency_overrides[backend_app.get_repository]()

    failing_report = CredentialHealthReport(
        workflow_id=UUID(workflow_id),
        results=[
            CredentialHealthResult(
                credential_id=uuid4(),
                name="Slack",
                provider="slack",
                status=CredentialHealthStatus.UNHEALTHY,
                last_checked_at=datetime.now(tz=UTC),
                failure_reason="token expired",
            )
        ],
        checked_at=datetime.now(tz=UTC),
    )

    class UnhealthyService:
        async def ensure_workflow_health(  # type: ignore[override]
            self,
            workflow_id: UUID,
            *,
            actor: str | None = None,
            workspace_id: str | None = None,
        ) -> CredentialHealthReport:
            return failing_report

    monkeypatch.setattr(repository, "_credential_service", UnhealthyService())

    response = api_client.post(
        f"/api/chatkit/workflows/{workflow_id}/trigger",
        json={
            "message": "Launch QA pipeline",
            "actor": "canvas-user",
            "client_thread_id": "thread-123",
            "metadata": {"priority": "high"},
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = response.json()["detail"]
    assert "unhealthy credentials" in detail["message"].lower()
