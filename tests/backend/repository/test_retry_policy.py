from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo.triggers.retry import RetryPolicyConfig
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowRunNotFoundError,
)


@pytest.mark.asyncio()
async def test_configure_retry_policy_and_schedule_decision(
    repository: WorkflowRepository,
) -> None:
    """Retry policy configuration surfaces scheduling decisions."""

    workflow = await repository.create_workflow(
        name="Retry Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )

    config = RetryPolicyConfig(
        max_attempts=2,
        initial_delay_seconds=10.0,
        jitter_factor=0.0,
    )
    stored = await repository.configure_retry_policy(workflow.id, config)
    assert stored.max_attempts == 2

    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="webhook",
        input_payload={},
        actor="tester",
    )

    first = await repository.schedule_retry_for_run(
        run.id, failed_at=datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    )
    assert first is not None
    assert first.retry_number == 1

    second = await repository.schedule_retry_for_run(
        run.id, failed_at=datetime(2025, 1, 1, 10, 10, tzinfo=UTC)
    )
    assert second is None


@pytest.mark.asyncio()
async def test_retry_configuration_requires_existing_workflow(
    repository: WorkflowRepository,
) -> None:
    """Retry configuration helpers enforce workflow existence."""

    missing_id = uuid4()
    with pytest.raises(WorkflowNotFoundError):
        await repository.configure_retry_policy(missing_id, RetryPolicyConfig())

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_retry_policy_config(missing_id)


@pytest.mark.asyncio()
async def test_schedule_retry_for_run_requires_existing_run(
    repository: WorkflowRepository,
) -> None:
    """Retry scheduling for unknown runs raises the expected error."""

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.schedule_retry_for_run(uuid4())


@pytest.mark.asyncio()
async def test_retry_policy_round_trip(
    repository: WorkflowRepository,
) -> None:
    """Retry policy configuration can be stored and retrieved."""

    workflow = await repository.create_workflow(
        name="Retry Policy",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="qa",
    )
    config = RetryPolicyConfig(max_attempts=4, initial_delay_seconds=12.5)

    stored = await repository.configure_retry_policy(workflow.id, config)
    assert stored.max_attempts == 4
    assert stored.initial_delay_seconds == 12.5

    fetched = await repository.get_retry_policy_config(workflow.id)
    assert fetched.max_attempts == 4
    assert fetched.initial_delay_seconds == 12.5
