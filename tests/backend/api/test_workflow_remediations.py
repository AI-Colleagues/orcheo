from __future__ import annotations
import asyncio
from fastapi.testclient import TestClient
from orcheo.models.workflow import (
    WorkflowDraftAccess,
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
)
from orcheo_backend.app.dependencies import get_repository


async def _seed_remediation_candidate() -> WorkflowRunRemediation:
    repository = get_repository()
    workflow = await repository.create_workflow(
        name="Remediation API Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph={"format": "langgraph_script", "source": "def build_graph(): ..."},
        metadata={},
        notes=None,
        created_by="tester",
    )
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="tester",
        input_payload={},
    )
    return await repository.create_remediation_candidate(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="api-route-fingerprint",
        version_checksum=version.compute_checksum(),
        graph_format="langgraph_script",
        context={"exception_type": "RuntimeError", "workflow_source": "source"},
    )


def test_workflow_remediation_routes_list_get_and_dismiss(
    api_client: TestClient,
) -> None:
    candidate = asyncio.run(_seed_remediation_candidate())

    list_response = api_client.get(
        "/api/workflow-remediations",
        params={
            "workflow_id": str(candidate.workflow_id),
            "run_id": str(candidate.run_id),
            "status": "pending",
            "limit": 10,
        },
    )

    assert list_response.status_code == 200
    listed = list_response.json()
    assert [item["id"] for item in listed] == [str(candidate.id)]

    get_response = api_client.get(f"/api/workflow-remediations/{candidate.id}")
    assert get_response.status_code == 200
    assert get_response.json()["fingerprint"] == "api-route-fingerprint"

    dismiss_response = api_client.post(
        f"/api/workflow-remediations/{candidate.id}/dismiss",
        json={"actor": "reviewer", "reason": "handled manually"},
    )
    assert dismiss_response.status_code == 200
    assert dismiss_response.json()["status"] == "dismissed"

    pending_response = api_client.get(
        "/api/workflow-remediations",
        params={"run_id": str(candidate.run_id), "status": "pending"},
    )
    assert pending_response.status_code == 200
    assert pending_response.json() == []


def test_workflow_remediation_dismiss_terminal_candidate_returns_conflict(
    api_client: TestClient,
) -> None:
    candidate = asyncio.run(_seed_remediation_candidate())
    repository = get_repository()
    asyncio.run(repository.claim_next_remediation_candidate(actor="worker"))
    asyncio.run(
        repository.mark_remediation_note_only(
            candidate.id,
            classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
            developer_note="Operator review required.",
            artifacts={},
        )
    )

    response = api_client.post(
        f"/api/workflow-remediations/{candidate.id}/dismiss",
        json={"actor": "reviewer"},
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "Only active or failed remediations can be dismissed."
    )
