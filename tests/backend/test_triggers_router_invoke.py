from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.types import Message
from orcheo.models import WorkflowRun, WorkflowVersion
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import triggers as triggers_router


def _make_request(
    *,
    method: str = "POST",
    body: bytes = b"",
    query_string: str = "",
    headers: dict[str, str] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "headers": [
            (key.encode("utf-8"), value.encode("utf-8"))
            for key, value in (headers or {}).items()
        ],
        "query_string": query_string.encode("utf-8"),
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _version(workflow_id: UUID) -> WorkflowVersion:
    return WorkflowVersion(
        workflow_id=workflow_id,
        version=1,
        graph={},
        created_by="tester",
    )


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_returns_immediate_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    request = _make_request(query_string="msg_signature=abc")

    class Repository:
        def __init__(self) -> None:
            self.handle_webhook_trigger = AsyncMock()

        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
            return _version(workflow_id)

    async def _fake_immediate_response(
        *_args: Any, **_kwargs: Any
    ) -> tuple[PlainTextResponse, bool]:
        return PlainTextResponse("ok"), False

    monkeypatch.setattr(
        triggers_router,
        "_try_immediate_response",
        _fake_immediate_response,
    )

    repository = Repository()
    response = await triggers_router.invoke_webhook_trigger(
        str(workflow_id),
        request,
        repository=repository,
        vault=object(),
    )

    assert isinstance(response, PlainTextResponse)
    assert response.body == b"ok"
    repository.handle_webhook_trigger.assert_not_awaited()


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_queues_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    request = _make_request(query_string="msg_signature=abc")
    run = WorkflowRun(
        workflow_version_id=uuid4(),
        triggered_by="webhook",
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
            return _version(workflow_id)

    async def _fake_immediate_response(
        *_args: Any, **_kwargs: Any
    ) -> tuple[None, bool]:
        return None, True

    async def _fake_queue_run(*_args: Any, **_kwargs: Any) -> WorkflowRun:
        return run

    monkeypatch.setattr(
        triggers_router,
        "_try_immediate_response",
        _fake_immediate_response,
    )
    monkeypatch.setattr(triggers_router, "_queue_webhook_run", _fake_queue_run)

    response = await triggers_router.invoke_webhook_trigger(
        str(workflow_id),
        request,
        repository=Repository(),
        vault=object(),
    )

    assert response is run


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_returns_accepted_when_no_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    request = _make_request(query_string="msg_signature=abc")

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
            return _version(workflow_id)

    async def _fake_immediate_response(
        *_args: Any, **_kwargs: Any
    ) -> tuple[None, bool]:
        return None, False

    monkeypatch.setattr(
        triggers_router,
        "_try_immediate_response",
        _fake_immediate_response,
    )

    response = await triggers_router.invoke_webhook_trigger(
        str(workflow_id),
        request,
        repository=Repository(),
        vault=object(),
    )

    assert response.status_code == 202
    assert response.body == b'{"status":"accepted"}'


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_reports_missing_workflow() -> None:
    workflow_id = uuid4()
    request = _make_request(query_string="msg_signature=abc")

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
            raise WorkflowNotFoundError("missing")

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router.invoke_webhook_trigger(
            str(workflow_id),
            request,
            repository=Repository(),
            vault=object(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"


@pytest.mark.asyncio()
async def test_invoke_webhook_trigger_reports_missing_version() -> None:
    workflow_id = uuid4()
    request = _make_request(query_string="msg_signature=abc")

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
            raise WorkflowVersionNotFoundError("missing version")

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router.invoke_webhook_trigger(
            str(workflow_id),
            request,
            repository=Repository(),
            vault=object(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow version not found"
