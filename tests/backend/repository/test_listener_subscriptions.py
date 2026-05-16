from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.listeners import (
    ListenerCursor,
    ListenerDispatchMessage,
    ListenerDispatchPayload,
    ListenerSubscriptionStatus,
)
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
)


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {
        "nodes": [],
        "edges": [],
        "index": {"listeners": list(listeners)},
    }


@pytest.mark.asyncio()
async def test_create_version_syncs_listener_subscriptions(
    repository: WorkflowRepository,
) -> None:
    workflow = await repository.create_workflow(
        name="Listener Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )

    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
                "allowed_updates": ["message"],
                "allowed_chat_types": ["private"],
                "poll_timeout_seconds": 30,
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )

    subscriptions = await repository.list_listener_subscriptions(
        workflow_id=workflow.id
    )
    assert len(subscriptions) == 1
    assert subscriptions[0].node_name == "telegram_listener"
    assert subscriptions[0].bot_identity_key == "telegram:[[telegram_one]]"


@pytest.mark.asyncio()
async def test_dispatch_listener_event_creates_listener_run(
    repository: WorkflowRepository,
) -> None:
    workflow = await repository.create_workflow(
        name="Dispatch Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    version = await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    run = await repository.dispatch_listener_event(
        subscription.id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="telegram:1",
            bot_identity=subscription.bot_identity_key,
            message=ListenerDispatchMessage(chat_id="123", text="hello"),
            reply_target={"chat_id": "123"},
            raw_event={"update_id": 1},
        ),
    )

    assert run is not None
    assert run.triggered_by == "listener"
    assert run.workflow_version_id == version.id
    assert run.input_payload["listener"]["reply_target"]["chat_id"] == "123"

    duplicate = await repository.dispatch_listener_event(
        subscription.id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="telegram:1",
            bot_identity=subscription.bot_identity_key,
            message=ListenerDispatchMessage(chat_id="123", text="hello"),
            reply_target={"chat_id": "123"},
            raw_event={"update_id": 1},
        ),
    )
    assert duplicate is None


@pytest.mark.asyncio()
async def test_listener_cursor_round_trip(repository: WorkflowRepository) -> None:
    workflow = await repository.create_workflow(
        name="Cursor Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    saved = await repository.save_listener_cursor(
        ListenerCursor(subscription_id=subscription.id, telegram_offset=42)
    )
    fetched = await repository.get_listener_cursor(subscription.id)

    assert saved.telegram_offset == 42
    assert fetched is not None
    assert fetched.telegram_offset == 42


@pytest.mark.asyncio()
async def test_archive_workflow_disables_listener_subscriptions(
    repository: WorkflowRepository,
) -> None:
    workflow = await repository.create_workflow(
        name="Archive Listener Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    archived = await repository.archive_workflow(workflow.id, actor="author")
    assert archived.is_archived is True

    disabled_subscription = await repository.get_listener_subscription(subscription.id)
    assert disabled_subscription.status == ListenerSubscriptionStatus.DISABLED
    assert disabled_subscription.assigned_runtime is None
    assert disabled_subscription.lease_expires_at is None


@pytest.mark.asyncio()
async def test_list_listener_subscriptions_without_workflow_id_filter(
    repository: WorkflowRepository,
) -> None:
    """list_listener_subscriptions with no filter returns subscriptions
    from all workflows."""
    graph = _listener_graph(
        {"node_name": "listener", "platform": "telegram", "token": "[[tok]]"}
    )
    workflow1 = await repository.create_workflow(
        name="Flow 1",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    workflow2 = await repository.create_workflow(
        name="Flow 2",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow1.id, graph=graph, metadata={}, notes=None, created_by="author"
    )
    await repository.create_version(
        workflow2.id, graph=graph, metadata={}, notes=None, created_by="author"
    )

    all_subs = await repository.list_listener_subscriptions()
    workflow1_subs = await repository.list_listener_subscriptions(
        workflow_id=workflow1.id
    )
    assert len(all_subs) >= len(workflow1_subs)
    assert len(all_subs) >= 2


@pytest.mark.asyncio()
async def test_get_listener_subscription_not_found(
    repository: WorkflowRepository,
) -> None:
    """get_listener_subscription raises WorkflowNotFoundError for unknown id."""
    with pytest.raises(WorkflowNotFoundError):
        await repository.get_listener_subscription(uuid4())


@pytest.mark.asyncio()
async def test_claim_listener_subscription_not_found(
    repository: WorkflowRepository,
) -> None:
    """claim_listener_subscription returns None when subscription id is unknown."""
    result = await repository.claim_listener_subscription(
        uuid4(), runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


@pytest.mark.asyncio()
async def test_claim_listener_subscription_inactive_status(
    repository: WorkflowRepository,
) -> None:
    """claim_listener_subscription returns None when subscription is not ACTIVE."""
    workflow = await repository.create_workflow(
        name="Claim Inactive",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    await repository.archive_workflow(workflow.id, actor="author")

    result = await repository.claim_listener_subscription(
        subscription.id, runtime_id="rt-1", lease_seconds=60
    )
    assert result is None


@pytest.mark.asyncio()
async def test_claim_listener_subscription_leased_by_other_runtime(
    repository: WorkflowRepository,
) -> None:
    """claim_listener_subscription returns None when another runtime holds a
    valid lease."""
    workflow = await repository.create_workflow(
        name="Claim Lease",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    claimed = await repository.claim_listener_subscription(
        subscription.id, runtime_id="runtime-A", lease_seconds=3600
    )
    assert claimed is not None

    blocked = await repository.claim_listener_subscription(
        subscription.id, runtime_id="runtime-B", lease_seconds=60
    )
    assert blocked is None


@pytest.mark.asyncio()
async def test_claim_and_release_listener_subscription(
    repository: WorkflowRepository,
) -> None:
    """Claim then release a subscription, verifying state transitions."""
    workflow = await repository.create_workflow(
        name="Claim Release",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    claimed = await repository.claim_listener_subscription(
        subscription.id, runtime_id="rt-1", lease_seconds=60
    )
    assert claimed is not None
    assert claimed.assigned_runtime == "rt-1"

    released = await repository.release_listener_subscription(
        subscription.id, runtime_id="rt-1"
    )
    assert released is not None
    assert released.assigned_runtime is None
    assert released.lease_expires_at is None


@pytest.mark.asyncio()
async def test_claim_listener_subscription_renewal_skips_duplicate_audit_entries(
    repository: WorkflowRepository,
) -> None:
    """Renewing a lease for the same runtime should not grow the audit log."""
    workflow = await repository.create_workflow(
        name="Claim Renewal",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    claimed = await repository.claim_listener_subscription(
        subscription.id, runtime_id="rt-1", lease_seconds=60
    )
    assert claimed is not None
    initial_audit_length = len(claimed.audit_log)

    renewed = await repository.claim_listener_subscription(
        subscription.id, runtime_id="rt-1", lease_seconds=60
    )
    assert renewed is not None
    assert renewed.assigned_runtime == "rt-1"
    assert len(renewed.audit_log) == initial_audit_length


@pytest.mark.asyncio()
async def test_release_listener_subscription_not_found(
    repository: WorkflowRepository,
) -> None:
    """release_listener_subscription returns None for an unknown subscription id."""
    result = await repository.release_listener_subscription(uuid4(), runtime_id="rt-1")
    assert result is None


@pytest.mark.asyncio()
async def test_release_listener_subscription_wrong_runtime(
    repository: WorkflowRepository,
) -> None:
    """release_listener_subscription returns None when runtime_id does not match."""
    workflow = await repository.create_workflow(
        name="Release Wrong",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    await repository.claim_listener_subscription(
        subscription.id, runtime_id="rt-owner", lease_seconds=60
    )

    result = await repository.release_listener_subscription(
        subscription.id, runtime_id="rt-other"
    )
    assert result is None


@pytest.mark.asyncio()
async def test_get_listener_cursor_not_found(
    repository: WorkflowRepository,
) -> None:
    """get_listener_cursor returns None when no cursor exists for the subscription."""
    result = await repository.get_listener_cursor(uuid4())
    assert result is None


@pytest.mark.asyncio()
async def test_dispatch_listener_event_subscription_not_found(
    repository: WorkflowRepository,
) -> None:
    """dispatch_listener_event raises WorkflowNotFoundError for unknown subscription."""
    with pytest.raises(WorkflowNotFoundError):
        await repository.dispatch_listener_event(
            uuid4(),
            ListenerDispatchPayload(
                platform="telegram",
                event_type="message",
                dedupe_key="k:1",
                bot_identity="telegram:[[tok]]",
                message=ListenerDispatchMessage(chat_id="1", text="hi"),
                reply_target={"chat_id": "1"},
                raw_event={},
            ),
        )


@pytest.mark.asyncio()
async def test_dispatch_listener_event_subscription_not_active(
    repository: WorkflowRepository,
) -> None:
    """dispatch_listener_event returns None when the subscription is not ACTIVE."""
    workflow = await repository.create_workflow(
        name="Dispatch Inactive",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    await repository.update_listener_subscription_status(
        subscription.id,
        status=ListenerSubscriptionStatus.DISABLED,
        actor="admin",
    )

    result = await repository.dispatch_listener_event(
        subscription.id,
        ListenerDispatchPayload(
            platform="telegram",
            event_type="message",
            dedupe_key="k:2",
            bot_identity=subscription.bot_identity_key,
            message=ListenerDispatchMessage(chat_id="1", text="hi"),
            reply_target={"chat_id": "1"},
            raw_event={},
        ),
    )
    assert result is None


@pytest.mark.asyncio()
async def test_update_listener_subscription_status_not_found(
    repository: WorkflowRepository,
) -> None:
    """update_listener_subscription_status raises WorkflowNotFoundError for
    unknown id."""
    with pytest.raises(WorkflowNotFoundError):
        await repository.update_listener_subscription_status(
            uuid4(),
            status=ListenerSubscriptionStatus.ACTIVE,
            actor="admin",
        )


@pytest.mark.asyncio()
async def test_update_listener_subscription_status_to_active_clears_error(
    repository: WorkflowRepository,
) -> None:
    """Setting status to ACTIVE clears the last_error field."""
    workflow = await repository.create_workflow(
        name="Status Active",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    disabled = await repository.update_listener_subscription_status(
        subscription.id,
        status=ListenerSubscriptionStatus.DISABLED,
        actor="admin",
    )
    assert disabled.status == ListenerSubscriptionStatus.DISABLED

    reactivated = await repository.update_listener_subscription_status(
        subscription.id,
        status=ListenerSubscriptionStatus.ACTIVE,
        actor="admin",
    )
    assert reactivated.status == ListenerSubscriptionStatus.ACTIVE
    assert reactivated.last_error is None


@pytest.mark.asyncio()
async def test_update_listener_subscription_status_to_disabled(
    repository: WorkflowRepository,
) -> None:
    """Setting status to DISABLED does not alter last_error when already None."""
    workflow = await repository.create_workflow(
        name="Status Disabled",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    result = await repository.update_listener_subscription_status(
        subscription.id,
        status=ListenerSubscriptionStatus.DISABLED,
        actor="admin",
    )
    assert result.status == ListenerSubscriptionStatus.DISABLED
    assert result.assigned_runtime is None
    assert result.lease_expires_at is None


@pytest.mark.asyncio()
async def test_update_listener_subscription_status_to_blocked_sets_error(
    repository: WorkflowRepository,
) -> None:
    """Setting status to BLOCKED stores the blocking reason."""
    workflow = await repository.create_workflow(
        name="Status Blocked",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    result = await repository.update_listener_subscription_status(
        subscription.id,
        status=ListenerSubscriptionStatus.BLOCKED,
        actor="admin",
        last_error="Credential 'tok' was not found in the configured vault",
    )
    assert result.status == ListenerSubscriptionStatus.BLOCKED
    assert "tok" in (result.last_error or "")


@pytest.mark.asyncio()
async def test_sync_listener_subscriptions_for_version(
    repository: WorkflowRepository,
) -> None:
    """sync_listener_subscriptions_for_version compiles and replaces subscriptions."""
    workflow = await repository.create_workflow(
        name="Sync Version",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    version = await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {"node_name": "tg", "platform": "telegram", "token": "[[tok]]"}
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subs_before = await repository.list_listener_subscriptions(workflow_id=workflow.id)
    assert len(subs_before) == 1

    new_graph = _listener_graph(
        {"node_name": "tg2", "platform": "telegram", "token": "[[tok2]]"}
    )
    await repository.sync_listener_subscriptions_for_version(
        workflow.id,
        version.id,
        new_graph,
        actor="author",
    )

    subs_after = await repository.list_listener_subscriptions(workflow_id=workflow.id)
    active_subs = [
        s for s in subs_after if s.status == ListenerSubscriptionStatus.ACTIVE
    ]
    assert len(active_subs) == 1
    assert active_subs[0].node_name == "tg2"
