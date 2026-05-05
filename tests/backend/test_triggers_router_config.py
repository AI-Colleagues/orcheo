"""Direct unit tests for trigger configuration router branches."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.repository.errors import WorkflowNotFoundError
from orcheo_backend.app.routers import triggers as triggers_router


class _WebhookConfigMissingRepo:
    def __init__(self, workflow_id: UUID) -> None:
        self._workflow_id = workflow_id

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del workflow_ref, include_archived
        return self._workflow_id

    async def configure_webhook_trigger(
        self, workflow_id: UUID, request: WebhookTriggerConfig
    ) -> WebhookTriggerConfig:
        del workflow_id, request
        raise WorkflowNotFoundError("missing")

    async def get_webhook_trigger_config(
        self, workflow_id: UUID
    ) -> WebhookTriggerConfig:
        del workflow_id
        raise WorkflowNotFoundError("missing")


class _CronConfigMissingRepo:
    def __init__(self, workflow_id: UUID) -> None:
        self._workflow_id = workflow_id

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> UUID:
        del workflow_ref, include_archived
        return self._workflow_id

    async def configure_cron_trigger(
        self, workflow_id: UUID, request: CronTriggerConfig
    ) -> CronTriggerConfig:
        del workflow_id, request
        raise WorkflowNotFoundError("missing")

    async def get_cron_trigger_config(self, workflow_id: UUID) -> CronTriggerConfig:
        del workflow_id
        raise WorkflowNotFoundError("missing")

    async def delete_cron_trigger(self, workflow_id: UUID) -> None:
        del workflow_id
        raise WorkflowNotFoundError("missing")


_MOCK_TENANT = SimpleNamespace(workspace_id=uuid4())


@pytest.mark.asyncio()
async def test_configure_webhook_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.configure_webhook_trigger(
            str(uuid4()),
            WebhookTriggerConfig(),
            _WebhookConfigMissingRepo(uuid4()),
            _MOCK_TENANT,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_webhook_trigger_config_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.get_webhook_trigger_config(
            str(uuid4()),
            _WebhookConfigMissingRepo(uuid4()),
            _MOCK_TENANT,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_configure_cron_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.configure_cron_trigger(
            str(uuid4()),
            CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_TENANT,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_cron_trigger_config_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.get_cron_trigger_config(
            str(uuid4()),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_TENANT,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_delete_cron_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.delete_cron_trigger(
            str(uuid4()),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_TENANT,
        )

    assert excinfo.value.status_code == 404
