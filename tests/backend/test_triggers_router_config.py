"""Direct unit tests for trigger configuration router branches."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import WorkflowRun
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.repository.errors import (
    CronTriggerNotFoundError,
    WorkflowNotFoundError,
)
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


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


@pytest.mark.asyncio()
async def test_configure_webhook_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.configure_webhook_trigger(
            str(uuid4()),
            WebhookTriggerConfig(),
            _WebhookConfigMissingRepo(uuid4()),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_webhook_trigger_config_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.get_webhook_trigger_config(
            str(uuid4()),
            _WebhookConfigMissingRepo(uuid4()),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_configure_cron_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.configure_cron_trigger(
            str(uuid4()),
            CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_cron_trigger_config_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.get_cron_trigger_config(
            str(uuid4()),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_delete_cron_trigger_translates_workflow_not_found() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.delete_cron_trigger(
            str(uuid4()),
            _CronConfigMissingRepo(uuid4()),
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_cron_trigger_config_translates_cron_not_found() -> None:
    """CronTriggerNotFoundError is translated to a 404."""
    workflow_id = uuid4()

    class _CronNotConfiguredRepo:
        async def resolve_workflow_ref(
            self,
            workflow_ref: str,
            *,
            include_archived: bool = True,
            workspace_id: str | None = None,
        ) -> UUID:
            del workflow_ref, include_archived
            return workflow_id

        async def get_cron_trigger_config(self, wid: UUID) -> CronTriggerConfig:
            raise CronTriggerNotFoundError("No cron trigger")

    with pytest.raises(HTTPException) as excinfo:
        await triggers_router.get_cron_trigger_config(
            str(workflow_id),
            _CronNotConfiguredRepo(),  # type: ignore[arg-type]
            _MOCK_WORKSPACE,
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio()
async def test_delete_cron_trigger_returns_204_on_success() -> None:
    """Successful cron trigger deletion returns HTTP 204."""
    workflow_id = uuid4()

    class _CronDeleteRepo:
        async def resolve_workflow_ref(
            self,
            workflow_ref: str,
            *,
            include_archived: bool = True,
            workspace_id: str | None = None,
        ) -> UUID:
            del workflow_ref, include_archived
            return workflow_id

        async def delete_cron_trigger(self, wid: UUID) -> None:
            pass

    response = await triggers_router.delete_cron_trigger(
        str(workflow_id),
        _CronDeleteRepo(),  # type: ignore[arg-type]
        _MOCK_WORKSPACE,
    )

    assert response.status_code == 204


@pytest.mark.asyncio()
async def test_dispatch_manual_runs_returns_runs_on_success() -> None:
    """Successful manual dispatch returns the list of created runs."""
    workflow_id = uuid4()
    run = WorkflowRun(
        workflow_version_id=uuid4(),
        triggered_by="manual",
        input_payload={},
    )

    class _SuccessRepo:
        async def dispatch_manual_runs(
            self, request: ManualDispatchRequest
        ) -> list[WorkflowRun]:
            return [run]

    request = ManualDispatchRequest(
        workflow_id=workflow_id,
        runs=[ManualDispatchItem(input_payload={})],
    )

    result = await triggers_router.dispatch_manual_runs(
        request=request,
        repository=_SuccessRepo(),  # type: ignore[arg-type]
    )

    assert result == [run]
