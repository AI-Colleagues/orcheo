from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess, WorkflowRun
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowVersionNotFoundError,
)
from .helpers import _remove_version


@pytest.mark.asyncio()
async def test_webhook_configuration_requires_workflow(
    repository: WorkflowRepository,
) -> None:
    """Configuring a webhook for a missing workflow raises an error."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.configure_webhook_trigger(uuid4(), WebhookTriggerConfig())


@pytest.mark.asyncio()
async def test_get_webhook_configuration_requires_workflow(
    repository: WorkflowRepository,
) -> None:
    """Fetching webhook config for a missing workflow raises an error."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_webhook_trigger_config(uuid4())


@pytest.mark.asyncio()
async def test_webhook_configuration_roundtrip(
    repository: WorkflowRepository,
) -> None:
    """Webhook configuration persists and returns deep copies."""

    workflow = await repository.create_workflow(
        name="Webhook",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    config = WebhookTriggerConfig(allowed_methods={"POST", "GET"})

    stored = await repository.configure_webhook_trigger(workflow.id, config)
    assert set(stored.allowed_methods) == {"POST", "GET"}

    fetched = await repository.get_webhook_trigger_config(workflow.id)
    assert fetched == stored


@pytest.mark.asyncio()
async def test_webhook_configuration_rejects_archived_workflow(
    repository: WorkflowRepository,
) -> None:
    """Archived workflows cannot receive new webhook triggers."""

    workflow = await repository.create_workflow(
        name="Archived Webhook Config",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.archive_workflow(workflow.id, actor="tester")

    with pytest.raises(WorkflowNotFoundError):
        await repository.configure_webhook_trigger(
            workflow.id,
            WebhookTriggerConfig(),
        )


@pytest.mark.asyncio()
async def test_handle_webhook_trigger_missing_resources(
    repository: WorkflowRepository,
) -> None:
    """Webhook handling raises when workflow or versions are missing."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.handle_webhook_trigger(
            uuid4(),
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )

    workflow = await repository.create_workflow(
        name="Webhook Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.handle_webhook_trigger(
            workflow.id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )

    version = await repository.create_version(
        workflow.id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="tester",
    )
    await repository.configure_webhook_trigger(workflow.id, WebhookTriggerConfig())

    await _remove_version(repository, version.id)

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.handle_webhook_trigger(
            workflow.id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )


@pytest.mark.asyncio()
async def test_handle_webhook_trigger_success(
    repository: WorkflowRepository,
) -> None:
    """A workflow with a version and webhook config dispatches a run."""
    workflow = await repository.create_workflow(
        name="Webhook Success",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="tester",
    )
    await repository.configure_webhook_trigger(workflow.id, WebhookTriggerConfig())

    run = await repository.handle_webhook_trigger(
        workflow.id,
        method="POST",
        headers={"content-type": "application/json"},
        query_params={},
        payload={"event": "ping"},
        source_ip="127.0.0.1",
    )

    assert isinstance(run, WorkflowRun)
    assert run.workflow_id == workflow.id
    assert run.triggered_by == "webhook"


@pytest.mark.asyncio()
async def test_handle_webhook_trigger_rejects_archived_workflow(
    repository: WorkflowRepository,
) -> None:
    """Archived workflows cannot be dispatched through webhook triggers."""

    workflow = await repository.create_workflow(
        name="Archived Webhook",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="tester",
    )
    await repository.configure_webhook_trigger(workflow.id, WebhookTriggerConfig())
    await repository.archive_workflow(workflow.id, actor="tester")

    with pytest.raises(WorkflowNotFoundError):
        await repository.handle_webhook_trigger(
            workflow.id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )
