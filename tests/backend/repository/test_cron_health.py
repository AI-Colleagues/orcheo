from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from orcheo.models.workflow import (
    CredentialHealthStatus,
    WorkflowDraftAccess,
)
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.vault.oauth import CredentialHealthReport, CredentialHealthResult
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    SqliteWorkflowRepository,
    WorkflowRepository,
)


class StubCredentialService:
    """Test double that simulates credential health responses."""

    def __init__(self) -> None:
        self.unhealthy_workflows: set[UUID] = set()
        self.checked_workflows: list[UUID] = []

    def mark_unhealthy(self, workflow_id: UUID) -> None:
        self.unhealthy_workflows.add(workflow_id)

    def is_workflow_healthy(self, workflow_id: UUID) -> bool:
        return True

    def get_report(self, workflow_id: UUID) -> CredentialHealthReport | None:
        return None

    async def ensure_workflow_health(
        self,
        workflow_id: UUID,
        *,
        actor: str | None = None,
        workspace_id: str | None = None,
    ) -> CredentialHealthReport:
        self.checked_workflows.append(workflow_id)
        status = (
            CredentialHealthStatus.UNHEALTHY
            if workflow_id in self.unhealthy_workflows
            else CredentialHealthStatus.HEALTHY
        )
        result = CredentialHealthResult(
            credential_id=uuid4(),
            name="stub",
            provider="stub",
            status=status,
            last_checked_at=datetime.now(tz=UTC),
            failure_reason=None
            if status is CredentialHealthStatus.HEALTHY
            else "invalid",
        )
        return CredentialHealthReport(
            workflow_id=workflow_id,
            results=[result],
            checked_at=datetime.now(tz=UTC),
        )


@pytest.mark.asyncio()
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_cron_dispatch_skips_unhealthy_workflows(
    backend: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Cron dispatch continues processing plans when health checks fail."""

    service = StubCredentialService()
    if backend == "memory":
        repository: WorkflowRepository = InMemoryWorkflowRepository(service)
    else:
        db_path = tmp_path_factory.mktemp("repo-health") / "workflows.sqlite"
        repository = SqliteWorkflowRepository(db_path, credential_service=service)

    try:
        unhealthy = await repository.create_workflow(
            name="Unhealthy Cron",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="owner",
        )
        healthy = await repository.create_workflow(
            name="Healthy Cron",
            slug=None,
            description=None,
            tags=None,
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="owner",
        )

        versions: dict[UUID, UUID] = {}
        for workflow in (unhealthy, healthy):
            version = await repository.create_version(
                workflow.id,
                graph={},
                metadata={},
                notes=None,
                created_by="owner",
            )
            versions[workflow.id] = version.id
            await repository.configure_cron_trigger(
                workflow.id,
                CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
            )

        service.mark_unhealthy(unhealthy.id)

        runs = await repository.dispatch_due_cron_runs(
            now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
        )

        assert [unhealthy.id, healthy.id] == service.checked_workflows
        assert len(runs) == 1
        assert runs[0].workflow_version_id == versions[healthy.id]
    finally:
        await repository.reset()
