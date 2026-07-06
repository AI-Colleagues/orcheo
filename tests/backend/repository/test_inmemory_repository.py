from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import pytest
from orcheo.listeners import (
    ListenerDedupeRecord,
    ListenerDispatchMessage,
    ListenerDispatchPayload,
    ListenerSubscriptionStatus,
)
from orcheo.models.base import _utcnow
from orcheo.models import WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.webhook import WebhookTriggerConfig
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
    WorkflowPublishStateError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState
from orcheo_backend.app.repository.in_memory.workflows import WorkflowCrudMixin


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {
        "nodes": [],
        "edges": [],
        "index": {"listeners": list(listeners)},
    }


@pytest.mark.asyncio()
async def test_inmemory_latest_version_missing_instance() -> None:
    """Missing latest version objects surface a dedicated error."""

    repository = InMemoryWorkflowRepository()

    workflow = await repository.create_workflow(
        name="Latest",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="tester",
    )

    repository._versions.pop(version.id)  # noqa: SLF001

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.get_latest_version(workflow.id)


@pytest.mark.asyncio()
async def test_inmemory_handle_webhook_missing_version_object() -> None:
    """Webhook dispatch raises when the latest version is missing."""

    repository = InMemoryWorkflowRepository()

    workflow = await repository.create_workflow(
        name="Webhook",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="tester",
    )
    await repository.configure_webhook_trigger(
        workflow.id, WebhookTriggerConfig(allowed_methods={"POST"})
    )

    repository._versions.pop(version.id)  # noqa: SLF001

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
async def test_inmemory_cron_dispatch_skips_missing_versions() -> None:
    """Cron dispatch ignores schedules when the latest version is missing."""

    repository = InMemoryWorkflowRepository()

    workflow = await repository.create_workflow(
        name="Cron",
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
    await repository.configure_cron_trigger(
        workflow.id, CronTriggerConfig(expression="0 12 * * *", timezone="UTC")
    )

    repository._versions.pop(version.id)  # noqa: SLF001

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    )
    assert runs == []


@pytest.mark.asyncio()
async def test_inmemory_cron_dispatch_normalizes_naive_now() -> None:
    """Naive dispatch timestamps are normalized to UTC before scheduling."""

    repository = InMemoryWorkflowRepository()
    captured_now: list[datetime] = []

    def _collect_due_cron_dispatches(*, now: datetime) -> list[object]:
        captured_now.append(now)
        return []

    repository._trigger_layer.collect_due_cron_dispatches = (  # type: ignore[method-assign]
        _collect_due_cron_dispatches
    )

    runs = await repository.dispatch_due_cron_runs(now=datetime(2025, 1, 1, 12, 0))

    assert runs == []
    assert captured_now[0].tzinfo == UTC


@pytest.mark.asyncio()
async def test_inmemory_manual_dispatch_skips_quota_exceeded_run() -> None:
    """Manual dispatch skips runs that hit the workspace quota."""

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Quota",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="tester",
    )

    def _raise_quota(**_: object) -> None:
        raise WorkspaceQuotaExceededError(
            "quota exceeded",
            code="workspace.quota.runs",
        )

    repository._create_run_locked = _raise_quota  # type: ignore[method-assign]

    from orcheo.triggers.manual import ManualDispatchRequest

    runs = await repository.dispatch_manual_runs(
        ManualDispatchRequest(
            workflow_id=workflow.id,
            runs=[{"workflow_version_id": version.id, "input_payload": {}}],
        )
    )

    assert runs == []


@pytest.mark.asyncio()
async def test_inmemory_publish_workflow_missing_id_raises_not_found() -> None:
    """publish_workflow raises WorkflowNotFoundError for unknown IDs."""

    repository = InMemoryWorkflowRepository()

    with pytest.raises(WorkflowNotFoundError):
        await repository.publish_workflow(
            uuid4(),
            require_login=False,
            actor="tester",
        )


@pytest.mark.asyncio()
async def test_inmemory_publish_workflow_translates_value_errors() -> None:
    """ValueError from Workflow.publish is surfaced as WorkflowPublishStateError."""

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Publish Twice",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.publish_workflow(
        workflow.id,
        require_login=False,
        actor="tester",
    )

    with pytest.raises(WorkflowPublishStateError):
        await repository.publish_workflow(
            workflow.id,
            require_login=False,
            actor="tester",
        )


@pytest.mark.asyncio()
async def test_inmemory_revoke_publish_missing_workflow() -> None:
    """revoke_publish raises WorkflowNotFoundError for unknown workflows."""

    repository = InMemoryWorkflowRepository()

    with pytest.raises(WorkflowNotFoundError):
        await repository.revoke_publish(uuid4(), actor="tester")


@pytest.mark.asyncio()
async def test_inmemory_revoke_publish_requires_published_state() -> None:
    """revoke_publish also translates invalid state errors."""

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Revoke",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    with pytest.raises(WorkflowPublishStateError):
        await repository.revoke_publish(workflow.id, actor="tester")


@pytest.mark.asyncio()
async def test_maybe_disable_no_listener_attribute() -> None:
    """Line 36: returns early when _disable_listener_subscriptions_locked is absent."""

    class MinimalRepo(WorkflowCrudMixin, InMemoryRepositoryState):
        pass

    repo = MinimalRepo()
    # Should return without error even though should_disable=True
    await repo._maybe_disable_listener_subscriptions(  # noqa: SLF001
        uuid4(), should_disable=True, actor="test"
    )


@pytest.mark.asyncio()
async def test_remove_cron_config_only_runs_when_archiving() -> None:
    """The archiving helper no-ops for non-archived updates."""

    class MinimalRepo(WorkflowCrudMixin, InMemoryRepositoryState):
        pass

    repo = MinimalRepo()
    calls: list[UUID] = []
    repo._trigger_layer.remove_cron_config = calls.append  # type: ignore[method-assign]

    workflow_id = uuid4()
    repo._remove_cron_config_if_archiving(workflow_id, is_archived=False)  # noqa: SLF001
    assert calls == []
    repo._remove_cron_config_if_archiving(workflow_id, is_archived=True)  # noqa: SLF001
    assert calls == [workflow_id]


@pytest.mark.asyncio()
async def test_inmemory_set_workflow_upload_error_rejects_archived_workflow() -> None:
    """set_workflow_upload_error raises WorkflowNotFoundError for archived workflows."""

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Upload Error",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.archive_workflow(workflow.id, actor="tester")

    with pytest.raises(WorkflowNotFoundError):
        await repository.set_workflow_upload_error(
            workflow.id,
            message="boom",
            actor="tester",
        )


@pytest.mark.asyncio()
async def test_maybe_disable_with_async_listener_method() -> None:
    """Line 39: awaits the result when
    _disable_listener_subscriptions_locked is async."""

    class AsyncListenerRepo(WorkflowCrudMixin, InMemoryRepositoryState):
        async def _disable_listener_subscriptions_locked(
            self, workflow_id: UUID, *, actor: str
        ) -> None:
            pass

    repo = AsyncListenerRepo()
    await repo._maybe_disable_listener_subscriptions(  # noqa: SLF001
        uuid4(), should_disable=True, actor="test"
    )


@pytest.mark.asyncio()
async def test_inmemory_disable_listeners_skips_missing_and_disabled() -> None:
    """Skip unknown and already-disabled subscriptions while disabling listeners."""
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Listener Disable",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="tester",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    stored = repository._listener_subscriptions[subscription.id]  # noqa: SLF001
    stored.status = ListenerSubscriptionStatus.DISABLED
    stored.assigned_runtime = "runtime-a"
    stored.lease_expires_at = _utcnow() + timedelta(seconds=120)
    existing_event_count = len(stored.audit_log)

    repository._workflow_listener_subscriptions[workflow.id].insert(0, uuid4())  # noqa: SLF001

    repository._disable_listener_subscriptions_locked(  # noqa: SLF001
        workflow.id,
        actor="tester",
    )

    disabled = repository._listener_subscriptions[subscription.id]  # noqa: SLF001
    assert disabled.assigned_runtime == "runtime-a"
    assert disabled.lease_expires_at is not None
    assert len(disabled.audit_log) == existing_event_count


@pytest.mark.asyncio()
async def test_inmemory_dispatch_listener_event_raises_when_version_missing() -> None:
    """Dispatch raises when the listener references a missing workflow version."""
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Dispatch Missing Version",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="tester",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    repository._versions.pop(version.id, None)  # noqa: SLF001

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.dispatch_listener_event(
            subscription.id,
            ListenerDispatchPayload(
                platform="telegram",
                event_type="message",
                dedupe_key="telegram:missing-version",
                bot_identity=subscription.bot_identity_key,
                message=ListenerDispatchMessage(chat_id="1", text="hello"),
                reply_target={"chat_id": "1"},
                raw_event={},
            ),
        )


@pytest.mark.asyncio()
async def test_inmemory_dispatch_listener_event_prunes_expired_dedupe() -> None:
    """Dispatch drops expired dedupe records before inserting the new key."""
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Dispatch Prune Dedupe",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="tester",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    repository._listener_dedupe[subscription.id] = {  # noqa: SLF001
        "expired": ListenerDedupeRecord(
            subscription_id=subscription.id,
            dedupe_key="expired",
            expires_at=_utcnow() - timedelta(seconds=1),
        )
    }

    run = await repository.dispatch_listener_event(
        subscription.id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="telegram:fresh",
            bot_identity=subscription.bot_identity_key,
            message=ListenerDispatchMessage(chat_id="1", text="hello"),
            reply_target={"chat_id": "1"},
            raw_event={},
        ),
    )

    assert run is not None
    assert "expired" not in repository._listener_dedupe[subscription.id]  # noqa: SLF001
    assert "telegram:fresh" in repository._listener_dedupe[subscription.id]  # noqa: SLF001


@pytest.mark.asyncio()
async def test_inmemory_dispatch_listener_event_passes_workspace_id() -> None:
    """Dispatch attaches the workflow's workspace_id so the run is accepted."""
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Dispatch Workspace",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="tester",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    repository._workflow_workspaces[workflow.id] = "ws-listener"  # noqa: SLF001

    captured: dict[str, object] = {}

    class _StubRun:
        def model_copy(self, *, deep: bool = False) -> _StubRun:
            return self

    def _capture(**kwargs: object) -> _StubRun:
        captured.update(kwargs)
        return _StubRun()

    repository._create_run_locked = _capture  # type: ignore[method-assign]  # noqa: SLF001

    await repository.dispatch_listener_event(
        subscription.id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="telegram:ws",
            bot_identity=subscription.bot_identity_key,
            message=ListenerDispatchMessage(chat_id="1", text="hello"),
            reply_target={"chat_id": "1"},
            raw_event={},
        ),
    )

    assert captured["workspace_id"] == "ws-listener"
    assert captured["triggered_by"] == "listener"
