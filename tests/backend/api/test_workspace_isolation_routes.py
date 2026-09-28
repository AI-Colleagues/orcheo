"""Routes that look resources up by ID must not cross workspace boundaries."""

from __future__ import annotations
import asyncio
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.workspace import Role, Workspace
from orcheo.workspace.models import WorkspaceContext
from orcheo_backend.app.dependencies import get_history_store
from orcheo_backend.app.workspace import get_workspace_repository
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context
from tests.backend.api.shared import create_workflow_with_version


def _switch_workspace(api_client: TestClient, *, role: Role = Role.OWNER) -> None:
    """Point subsequent requests at a second, unrelated workspace."""
    other = Workspace(id=uuid4(), slug=f"other-{uuid4().hex[:8]}", name="Other")
    get_workspace_repository().create_workspace(other)
    context = WorkspaceContext(
        workspace_id=other.id,
        workspace_slug=other.slug,
        user_id="anonymous",
        role=role,
    )
    api_client.app.dependency_overrides[resolve_workspace_context] = lambda: context


@pytest.fixture()
def foreign_resources(api_client: TestClient) -> dict[str, str]:
    """Create a workflow, run, history and credential in the default workspace."""
    workflow_id, version_id = create_workflow_with_version(api_client)
    run = api_client.post(
        f"/api/workflows/{workflow_id}/runs",
        json={"workflow_version_id": version_id, "triggered_by": "tester"},
    ).json()
    asyncio.run(
        get_history_store().start_run(workflow_id=workflow_id, execution_id=run["id"])
    )
    credential = api_client.post(
        "/api/credentials",
        json={
            "name": "foreign_key",
            "provider": "openai",
            "secret": "sk-foreign",
            "access": "shared",
        },
    ).json()
    return {
        "workflow_id": workflow_id,
        "run_id": run["id"],
        "credential_id": credential["id"],
    }


def test_owner_workspace_can_still_reach_its_resources(
    api_client: TestClient, foreign_resources: dict[str, str]
) -> None:
    run_id = foreign_resources["run_id"]
    credential_id = foreign_resources["credential_id"]

    assert api_client.get(f"/api/executions/{run_id}/trace").status_code == 200
    secret = api_client.get(f"/api/credentials/{credential_id}/secret")
    assert secret.json()["secret"] == "sk-foreign"
    started = api_client.post(f"/api/runs/{run_id}/start", json={"actor": "t"})
    assert started.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/credentials/{credential_id}/secret", None),
        ("patch", "/api/credentials/{credential_id}", {"name": "stolen"}),
        ("delete", "/api/credentials/{credential_id}", None),
    ],
)
def test_foreign_credentials_are_not_found(
    api_client: TestClient,
    foreign_resources: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    _switch_workspace(api_client)

    response = api_client.request(method, path.format(**foreign_resources), json=body)

    # Not found rather than forbidden, so credential IDs from other
    # workspaces cannot be probed for existence.
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/runs/{run_id}/start", {"actor": "t"}),
        ("post", "/api/runs/{run_id}/succeed", {"actor": "t"}),
        ("post", "/api/runs/{run_id}/fail", {"actor": "t", "error": "x"}),
        ("post", "/api/runs/{run_id}/cancel", {"actor": "t"}),
        ("get", "/api/executions/{run_id}/history", None),
        ("get", "/api/executions/{run_id}/trace", None),
        ("post", "/api/executions/{run_id}/replay", {"from_step": 0}),
        (
            "post",
            "/api/triggers/manual/dispatch",
            {"workflow_id": "{workflow_id}", "runs": [{}]},
        ),
        (
            "post",
            "/api/nodes/execute",
            {
                "node_config": {"type": "SetVariableNode"},
                "workflow_id": "{workflow_id}",
            },
        ),
    ],
)
def test_foreign_runs_and_workflows_are_not_found(
    api_client: TestClient,
    foreign_resources: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    _switch_workspace(api_client)
    if body is not None:
        body = {
            key: value.format(**foreign_resources) if isinstance(value, str) else value
            for key, value in body.items()
        }

    response = api_client.request(method, path.format(**foreign_resources), json=body)

    assert response.status_code == 404


def test_cron_dispatch_requires_workspace_admin(api_client: TestClient) -> None:
    _switch_workspace(api_client, role=Role.EDITOR)
    denied = api_client.post("/api/triggers/cron/dispatch", json={})
    _switch_workspace(api_client, role=Role.ADMIN)
    allowed = api_client.post("/api/triggers/cron/dispatch", json={})

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_node_execution_cannot_resolve_foreign_credentials(
    api_client: TestClient, foreign_resources: dict[str, str]
) -> None:
    body = {
        "node_config": {
            "type": "SetVariableNode",
            "name": "probe",
            "variables": {"key": "[[foreign_key]]"},
        }
    }
    own = api_client.post("/api/nodes/execute", json=body)
    _switch_workspace(api_client)
    foreign = api_client.post("/api/nodes/execute", json=body)

    assert own.json()["result"] == {"key": "sk-foreign"}
    assert "sk-foreign" not in foreign.text


def test_cron_dispatch_only_fires_the_admins_workspace(
    api_client: TestClient, foreign_resources: dict[str, str]
) -> None:
    workflow_id = foreign_resources["workflow_id"]
    configured = api_client.put(
        f"/api/workflows/{workflow_id}/triggers/cron/config",
        json={"expression": "0 * * * *", "timezone": "UTC"},
    )
    assert configured.status_code == 200, configured.text
    due = {"now": "2099-01-01T00:00:00Z"}

    _switch_workspace(api_client, role=Role.ADMIN)
    foreign = api_client.post("/api/triggers/cron/dispatch", json=due)
    api_client.app.dependency_overrides.pop(resolve_workspace_context)
    own = api_client.post("/api/triggers/cron/dispatch", json=due)

    assert foreign.status_code == 200
    assert foreign.json() == []
    assert [run["workflow_id"] for run in own.json()] == [workflow_id]
