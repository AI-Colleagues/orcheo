"""Gallery summary batching, missing versions, and workspace isolation."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from tests.backend.repository.test_postgres_repository import (
    _version_payload,
    make_repository,
)


@pytest.mark.asyncio
async def test_postgres_summaries_use_one_scoped_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any gallery size uses one summary query, including versionless workflows."""
    ids = [uuid4() for _ in range(100)]
    rows = [
        {
            "id": str(workflow_id),
            "version_payload": (
                _version_payload(uuid4(), workflow_id) if index else None
            ),
            "is_scheduled": index == 1,
        }
        for index, workflow_id in enumerate(ids)
    ]
    repo = make_repository(monkeypatch, [{"rows": rows}])
    summaries = await repo.get_workflow_summaries(ids, workspace_id="workspace-a")
    connection = repo._pool._connection
    assert len(connection.queries) == 1
    query, params = connection.queries[0]
    assert "w.workspace_id = %s" in query
    assert "LEFT JOIN LATERAL" in query
    assert params == ([str(workflow_id) for workflow_id in ids], "workspace-a")
    assert len(summaries) == 100
    assert summaries[ids[0]] == (None, False)
    assert summaries[ids[1]][1] is True
    assert summaries[ids[1]][0].workflow_id == ids[1]
    assert await repo.get_workflow_summaries([], workspace_id="workspace-a") == {}
    assert len(connection.queries) == 1


@pytest.mark.asyncio
async def test_inmemory_summaries_are_scoped_and_return_latest_copies() -> None:
    """Summaries omit other workspaces and preserve detached latest versions."""
    repo = InMemoryWorkflowRepository()
    workflows = []
    for workspace_id in ("workspace-a", "workspace-a", "workspace-b"):
        workflows.append(
            await repo.create_workflow(
                name=str(uuid4()),
                slug=None,
                description=None,
                tags=None,
                draft_access=WorkflowDraftAccess.PERSONAL,
                actor="tester",
                workspace_id=workspace_id,
            )
        )
    for _ in range(2):
        latest = await repo.create_version(
            workflows[0].id,
            graph={"nodes": [], "edges": []},
            metadata={},
            notes=None,
            created_by="tester",
        )
    await repo.configure_cron_trigger(
        workflows[0].id, CronTriggerConfig(expression="*/5 * * * *")
    )
    summaries = await repo.get_workflow_summaries(
        [workflow.id for workflow in workflows] + [uuid4()], workspace_id="workspace-a"
    )
    assert set(summaries) == {workflow.id for workflow in workflows[:2]}
    version, scheduled = summaries[workflows[0].id]
    assert version is not None and version.id == latest.id and version.version == 2
    assert scheduled
    assert summaries[workflows[1].id] == (None, False)
    version.notes = "modified"
    assert (await repo.get_latest_version(workflows[0].id)).notes is None
