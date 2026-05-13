"""Cover missing branches in routers/triggers.py:
- WorkspaceQuotaExceededError in dispatch_cron_triggers (487-488)
- WorkspaceQuotaExceededError in dispatch_manual_runs (512-513)
- except Exception in _invoke_workspace_webhook (534-535)
- WorkspaceRateLimitError in _invoke_workspace_webhook (543-544)
- Slack URL verification in _invoke_workspace_webhook (571)
- Immediate response checks in _invoke_workspace_webhook (583-610)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import triggers as triggers_router


# ---------------------------------------------------------------------------
# dispatch_cron_triggers – WorkspaceQuotaExceededError (lines 487-488)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_cron_triggers_quota_exceeded_raises_http() -> None:
    class Repository:
        async def dispatch_due_cron_runs(self, *, now=None):
            raise WorkspaceQuotaExceededError(
                "Quota exceeded",
                code="workspace.quota.concurrent_runs",
                details={"limit": 1, "current": 1},
            )

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router.dispatch_cron_triggers(
            repository=Repository(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# dispatch_manual_runs – WorkspaceQuotaExceededError (lines 512-513)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_manual_runs_quota_exceeded_raises_http() -> None:
    from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest

    class Repository:
        async def dispatch_manual_runs(self, request):
            raise WorkspaceQuotaExceededError(
                "Quota exceeded",
                code="workspace.quota.concurrent_runs",
                details={"limit": 1, "current": 1},
            )

    request = ManualDispatchRequest(workflow_id=uuid4(), runs=[ManualDispatchItem()])

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router.dispatch_manual_runs(
            request=request,
            repository=Repository(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Shared mocks for _invoke_workspace_webhook tests
# ---------------------------------------------------------------------------


class _MockRequest:
    method = "POST"
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = None

    async def body(self) -> bytes:
        return b""


class _MockRepository:
    def __init__(self, workflow_id=None) -> None:
        self._workflow_id = workflow_id or uuid4()

    async def resolve_workflow_ref(
        self, ref, *, include_archived=True, workspace_id=None
    ):
        return self._workflow_id

    async def handle_webhook_trigger(self, workflow_id, **kwargs):
        return SimpleNamespace(triggered_by="webhook")


class _MockVault:
    pass


# ---------------------------------------------------------------------------
# except Exception for workspace lookup (lines 534-535)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_unexpected_workspace_error_returns_404() -> (
    None
):
    """A non-WorkspaceNotFoundError during slug lookup still returns 404."""

    class BoomWorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: (_ for _ in ()).throw(
                RuntimeError("unexpected")
            )
        )

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router._invoke_workspace_webhook(
            "slug",
            "trigger",
            _MockRequest(),  # type: ignore[arg-type]
            _MockRepository(),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            BoomWorkspaceService(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# WorkspaceRateLimitError in _invoke_workspace_webhook (lines 543-544)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_rate_limit_raises_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    class Governance:
        def check_api_rate_limit(self, wid: str) -> None:
            raise WorkspaceRateLimitError(
                "Too many requests",
                code="workspace.rate_limited",
                retry_after=60,
            )

    monkeypatch.setattr(
        triggers_router, "get_workspace_governance", lambda: Governance()
    )

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router._invoke_workspace_webhook(
            "slug",
            "trigger",
            _MockRequest(),  # type: ignore[arg-type]
            _MockRepository(),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            WorkspaceService(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Slack URL verification in _invoke_workspace_webhook (line 571)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_handles_slack_url_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    workspace_id = str(uuid4())
    workflow_id = uuid4()

    class SlackRequest(_MockRequest):
        async def body(self) -> bytes:
            return json.dumps(
                {"type": "url_verification", "challenge": "my-challenge"}
            ).encode()

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    monkeypatch.setattr(
        triggers_router,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda wid: None),
    )

    response = await triggers_router._invoke_workspace_webhook(
        "slug",
        "trigger",
        SlackRequest(),  # type: ignore[arg-type]
        _MockRepository(workflow_id),  # type: ignore[arg-type]
        _MockVault(),  # type: ignore[arg-type]
        WorkspaceService(),  # type: ignore[arg-type]
    )

    assert isinstance(response, JSONResponse)
    import json as _json

    body = _json.loads(response.body)
    assert body["challenge"] == "my-challenge"


# ---------------------------------------------------------------------------
# Immediate response with should_queue=True — return both response and run
# (lines 583-591, 594->606, 607)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_immediate_response_with_should_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _should_try_immediate_response fires and returns a response with
    should_process=True, the run is also queued but the immediate response is returned."""

    workspace_id = str(uuid4())
    workflow_id = uuid4()

    class WeCom(_MockRequest):
        query_params = {"msg_signature": "abc", "timestamp": "1", "nonce": "xyz"}

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    class Repository(_MockRepository):
        def __init__(self):
            super().__init__(workflow_id)

        async def get_latest_version(self, wid):
            from orcheo.models.workflow import WorkflowVersion

            return WorkflowVersion(
                workflow_id=wid, version=1, graph={}, created_by="tester"
            )

    async def _fake_immediate(
        *args: Any, **kwargs: Any
    ) -> tuple[PlainTextResponse, bool]:
        return PlainTextResponse("hello"), True

    monkeypatch.setattr(triggers_router, "_try_immediate_response", _fake_immediate)
    monkeypatch.setattr(
        triggers_router,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda wid: None),
    )

    response = await triggers_router._invoke_workspace_webhook(
        "slug",
        "trigger",
        WeCom(),  # type: ignore[arg-type]
        Repository(),  # type: ignore[arg-type]
        _MockVault(),  # type: ignore[arg-type]
        WorkspaceService(),  # type: ignore[arg-type]
    )

    assert isinstance(response, PlainTextResponse)
    assert response.body == b"hello"


# ---------------------------------------------------------------------------
# Immediate response check: workflow not found (line 588-589)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_immediate_response_workflow_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())
    workflow_id = uuid4()

    class WeCom(_MockRequest):
        query_params = {"msg_signature": "abc"}

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    class Repository(_MockRepository):
        def __init__(self):
            super().__init__(workflow_id)

        async def get_latest_version(self, wid):
            raise WorkflowNotFoundError("missing")

    monkeypatch.setattr(
        triggers_router,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda wid: None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router._invoke_workspace_webhook(
            "slug",
            "trigger",
            WeCom(),  # type: ignore[arg-type]
            Repository(),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            WorkspaceService(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Immediate response check: workflow version not found (line 590-591)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_immediate_response_version_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())
    workflow_id = uuid4()

    class WeCom(_MockRequest):
        query_params = {"msg_signature": "abc"}

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    class Repository(_MockRepository):
        def __init__(self):
            super().__init__(workflow_id)

        async def get_latest_version(self, wid):
            raise WorkflowVersionNotFoundError("missing version")

    monkeypatch.setattr(
        triggers_router,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda wid: None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await triggers_router._invoke_workspace_webhook(
            "slug",
            "trigger",
            WeCom(),  # type: ignore[arg-type]
            Repository(),  # type: ignore[arg-type]
            _MockVault(),  # type: ignore[arg-type]
            WorkspaceService(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# should_queue=False and no immediate_response → JSONResponse (line 610)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_workspace_webhook_no_run_no_immediate_returns_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When should_queue=False and no immediate_response, return {status: accepted}."""

    workspace_id = str(uuid4())
    workflow_id = uuid4()

    class WeCom(_MockRequest):
        query_params = {"msg_signature": "abc"}

    class WorkspaceService:
        repository = SimpleNamespace(
            get_workspace_by_slug=lambda slug: SimpleNamespace(id=workspace_id)
        )

    class Repository(_MockRepository):
        def __init__(self):
            super().__init__(workflow_id)

        async def get_latest_version(self, wid):
            from orcheo.models.workflow import WorkflowVersion

            return WorkflowVersion(
                workflow_id=wid, version=1, graph={}, created_by="tester"
            )

    async def _fake_immediate(*args: Any, **kwargs: Any) -> tuple[None, bool]:
        return None, False  # no immediate response, don't queue

    monkeypatch.setattr(triggers_router, "_try_immediate_response", _fake_immediate)
    monkeypatch.setattr(
        triggers_router,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda wid: None),
    )

    response = await triggers_router._invoke_workspace_webhook(
        "slug",
        "trigger",
        WeCom(),  # type: ignore[arg-type]
        Repository(),  # type: ignore[arg-type]
        _MockVault(),  # type: ignore[arg-type]
        WorkspaceService(),  # type: ignore[arg-type]
    )

    assert isinstance(response, JSONResponse)
    import json

    body = json.loads(response.body)
    assert body["status"] == "accepted"
