"""Tests for Telegram listener normalization and polling."""

from __future__ import annotations
import pytest
from orcheo.listeners import (
    ListenerDispatchPayload,
    TelegramPollingAdapter,
    normalize_telegram_update,
)
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import InMemoryWorkflowRepository


class FakeTelegramClient:
    def __init__(self, updates: list[dict[str, object]]) -> None:
        self.updates = updates
        self.calls: list[dict[str, object]] = []

    async def get_updates(
        self,
        *,
        token: str,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
        limit: int,
    ) -> list[dict[str, object]]:
        self.calls.append(
            {
                "token": token,
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": allowed_updates,
                "limit": limit,
            }
        )
        return list(self.updates)


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {"nodes": [], "edges": [], "index": {"listeners": list(listeners)}}


@pytest.mark.asyncio()
async def test_normalize_telegram_update_matches_parser_shape() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Telegram Flow",
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
                "token": "[[telegram_token]]",
                "allowed_updates": ["message"],
                "allowed_chat_types": ["private"],
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]

    payload = normalize_telegram_update(
        subscription,
        {
            "update_id": 11,
            "message": {
                "message_id": 22,
                "text": "hello",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456, "first_name": "Alice"},
            },
        },
    )

    assert isinstance(payload, ListenerDispatchPayload)
    assert payload.message.chat_id == "123"
    assert payload.message.user_id == "456"
    assert payload.message.text == "hello"
    assert payload.reply_target == {"chat_id": "123"}


@pytest.mark.asyncio()
async def test_telegram_polling_adapter_persists_offset_and_dedupes() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Telegram Flow",
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
                "token": "[[telegram_token]]",
                "allowed_updates": ["message"],
                "allowed_chat_types": ["private"],
                "poll_timeout_seconds": 10,
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    client = FakeTelegramClient(
        [
            {
                "update_id": 11,
                "message": {
                    "message_id": 22,
                    "text": "hello",
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 456, "first_name": "Alice"},
                },
            }
        ]
    )
    adapter = TelegramPollingAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="runtime-1",
        client=client,
    )

    offset = await adapter.poll_once(token="[[telegram_token]]", offset=None)
    assert offset == 12
    cursor = await repository.get_listener_cursor(subscription.id)
    assert cursor is not None
    assert cursor.telegram_offset == 12
    runs = await repository.list_runs_for_workflow(workflow.id)
    assert len(runs) == 1

    await adapter.poll_once(token="[[telegram_token]]", offset=12)
    reruns = await repository.list_runs_for_workflow(workflow.id)
    assert len(reruns) == 1


@pytest.mark.asyncio()
async def test_telegram_polling_adapter_respects_saved_offset() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Telegram Flow",
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
            },
            {
                "node_name": "telegram_listener_two",
                "platform": "telegram",
                "token": "[[telegram_two]]",
            },
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscriptions = await repository.list_listener_subscriptions(
        workflow_id=workflow.id
    )
    assert len(subscriptions) == 2
    assert {sub.bot_identity_key for sub in subscriptions} == {
        "telegram:[[telegram_one]]",
        "telegram:[[telegram_two]]",
    }


@pytest.mark.asyncio()
async def test_telegram_polling_adapter_two_independent_configs() -> None:
    repository = InMemoryWorkflowRepository()
    workflow_one = await repository.create_workflow(
        name="Telegram Flow One",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    workflow_two = await repository.create_workflow(
        name="Telegram Flow Two",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow_one.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener_one",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    await repository.create_version(
        workflow_two.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener_two",
                "platform": "telegram",
                "token": "[[telegram_two]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )
    subscriptions = await repository.list_listener_subscriptions()
    assert len(subscriptions) == 2
    assert {sub.bot_identity_key for sub in subscriptions} == {
        "telegram:[[telegram_one]]",
        "telegram:[[telegram_two]]",
    }
