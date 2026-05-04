"""Tests for workflow router helper utilities."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.repository.errors import WorkflowNotFoundError
from orcheo_backend.app.routers import workflows as workflow_router
from orcheo_backend.app.routers.workflows import get_workflow_canvas


@pytest.mark.parametrize(
    "graph",
    [
        None,
        "not a dict",
        42,
        {"index": None},
        {"index": {"mermaid": 123}},
        {"index": {"mermaid": ""}},
        {"index": {"mermaid": "   "}},
        {"index": {"other": "value"}},
    ],
)
def test_extract_index_mermaid_handles_invalid_graphs(graph: object) -> None:
    assert workflow_router._extract_index_mermaid(graph) is None


def test_extract_index_mermaid_returns_mermaid_string() -> None:
    mermaid = "graph LR"
    graph = {"index": {"mermaid": mermaid}}

    assert workflow_router._extract_index_mermaid(graph) == mermaid


def test_extract_index_mermaid_preserves_whitespace() -> None:
    mermaid = "  graph LR    "
    graph = {"index": {"mermaid": mermaid}}

    assert workflow_router._extract_index_mermaid(graph) == mermaid


@pytest.mark.parametrize(
    ("graph", "expected"),
    [
        (None, False),
        ("not a dict", False),
        ({"index": {"cron": []}}, False),
        ({"index": {"cron": [{"cron": "* * * * *"}]}}, True),
        ({"nodes": [{"type": "CronTriggerNode"}]}, True),
        ({"summary": {"nodes": [{"type": "CronTriggerNode"}]}}, True),
        ({"nodes": [{"type": "AINode"}], "summary": {"nodes": []}}, False),
    ],
)
def test_graph_has_cron_trigger_detects_supported_shapes(
    graph: object, expected: bool
) -> None:
    assert workflow_router._graph_has_cron_trigger(graph) is expected


class _MissingWorkflowRepository:
    """Repository that resolves workflows but fails to load them."""

    def __init__(self) -> None:
        self._workflow_id = uuid4()

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:  # pragma: no cover - stub
        return self._workflow_id

    async def get_workflow(
        self, workflow_id: UUID, *, tenant_id=None
    ) -> None:  # pragma: no cover - stub
        raise WorkflowNotFoundError(str(workflow_id))

    async def list_versions(
        self, workflow_id: UUID
    ) -> list[object]:  # pragma: no cover - stub
        return []


_MOCK_TENANT = SimpleNamespace(tenant_id=uuid4())


@pytest.mark.asyncio()
async def test_get_workflow_canvas_returns_not_found_when_missing() -> None:
    repository = _MissingWorkflowRepository()

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_canvas("canvas-flow", repository, _MOCK_TENANT)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"


def test_resolve_draft_access_returns_requested_value_when_valid() -> None:
    result = workflow_router._resolve_draft_access(
        WorkflowDraftAccess.PERSONAL,
        ["shared"],
        None,
    )

    assert result is WorkflowDraftAccess.PERSONAL


def test_resolve_draft_access_defaults_to_authenticated_for_user_context() -> None:
    context = RequestContext(
        subject="auth0|user",
        identity_type="user",
        scopes=frozenset(),
        workspace_ids=frozenset(),
    )

    result = workflow_router._resolve_draft_access(None, [], context)

    assert result is WorkflowDraftAccess.AUTHENTICATED


def test_resolve_draft_access_defaults_to_workspace_when_context_has_workspace_ids() -> (  # noqa: E501
    None
):
    context = RequestContext(
        subject="service|token",
        identity_type="service",
        scopes=frozenset(),
        workspace_ids=frozenset({"team-x"}),
    )

    result = workflow_router._resolve_draft_access(None, ["shared"], context)

    assert result is WorkflowDraftAccess.WORKSPACE
