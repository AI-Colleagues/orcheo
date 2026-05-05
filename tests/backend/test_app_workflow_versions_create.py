"""Tests for ingesting workflow versions and config-only updates."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import WorkflowVersion
from orcheo_backend.app import (
    ingest_workflow_version,
    update_workflow_version_runnable_config,
)
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import workflows as workflow_routes
from orcheo_backend.app.schemas.workflows import (
    WorkflowVersionIngestRequest,
    WorkflowVersionRunnableConfigUpdateRequest,
)


_MOCK_TENANT = SimpleNamespace(workspace_id=uuid4())


@pytest.mark.asyncio()
async def test_ingest_workflow_version_success() -> None:
    """Ingest workflow version creates version from script."""

    workflow_id = uuid4()
    version_id = uuid4()
    captured_config: dict[str, object] | None = None

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            nonlocal captured_config
            captured_config = runnable_config
            return WorkflowVersion(
                id=version_id,
                workflow_id=wf_id,
                version=1,
                graph=graph,
                created_by=created_by,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    script_code = (
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('test', lambda x: x)"
    )
    request = WorkflowVersionIngestRequest(
        script=script_code,
        entrypoint="graph",
        runnable_config={"tags": ["ingest"]},
        created_by="admin",
    )

    result = await ingest_workflow_version(
        str(workflow_id), request, Repository(), _MOCK_TENANT
    )

    assert result.id == version_id
    assert captured_config == {"tags": ["ingest"]}


@pytest.mark.asyncio()
async def test_ingest_workflow_version_script_error() -> None:
    """Ingest workflow version handles script ingestion errors."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph=graph,
                created_by=created_by,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowVersionIngestRequest(
        script="invalid python code {",
        entrypoint="graph",
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_TENANT
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio()
async def test_ingest_workflow_version_rejects_missing_required_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Template ingest fails early when required plugins are unavailable."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            del wf_id, graph, metadata, notes, created_by, runnable_config
            raise AssertionError("create_version should not be called")

    monkeypatch.setattr(
        workflow_routes,
        "missing_required_plugins",
        lambda required_plugins: list(required_plugins),
    )

    request = WorkflowVersionIngestRequest(
        script="from langgraph.graph import StateGraph\ngraph = StateGraph(dict)",
        entrypoint="graph",
        metadata={
            "template": {
                "requiredPlugins": ["orcheo-plugin-lark-listener"],
            }
        },
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_TENANT
        )

    assert exc_info.value.status_code == 400
    assert "orcheo-plugin-lark-listener" in str(exc_info.value.detail)


@pytest.mark.asyncio()
async def test_ingest_workflow_version_not_found() -> None:
    """Ingest workflow version raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            raise WorkflowNotFoundError("not found")

    script_code = (
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('test', lambda x: x)"
    )
    request = WorkflowVersionIngestRequest(
        script=script_code,
        entrypoint="graph",
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_TENANT
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_success() -> None:
    """Version runnable-config endpoint persists config-only updates."""

    workflow_id = uuid4()
    version_id = uuid4()
    captured_actor: str | None = None
    captured_version: int | None = None
    captured_config: dict[str, object] | None = None

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            nonlocal captured_actor, captured_version, captured_config
            captured_actor = actor
            captured_version = version_number
            captured_config = runnable_config
            return WorkflowVersion(
                id=version_id,
                workflow_id=wf_id,
                version=version_number,
                graph={
                    "format": "langgraph-script",
                    "source": (
                        "from langgraph.graph import StateGraph\ngraph=StateGraph(dict)"
                    ),
                },
                created_by="admin",
                runnable_config=runnable_config,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["canvas"], "run_name": "canvas-save"},
        actor="canvas-user",
    )

    result = await update_workflow_version_runnable_config(
        str(workflow_id),
        3,
        request,
        Repository(),
        _MOCK_TENANT,
    )

    assert result.id == version_id
    assert captured_actor == "canvas-user"
    assert captured_version == 3
    assert captured_config == {"tags": ["canvas"], "run_name": "canvas-save"}


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_missing_version() -> None:
    """Version runnable-config endpoint maps missing versions to 404."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            del wf_id, version_number, runnable_config, actor
            raise WorkflowVersionNotFoundError("v99")

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["x"]},
        actor="cli",
    )
    with pytest.raises(HTTPException) as exc_info:
        await update_workflow_version_runnable_config(
            str(workflow_id),
            99,
            request,
            Repository(),
            _MOCK_TENANT,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_missing_workflow() -> None:
    """Version runnable-config endpoint maps missing workflows to 404."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            del wf_id, version_number, runnable_config, actor
            raise WorkflowNotFoundError("wf-missing")

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["x"]},
        actor="cli",
    )
    with pytest.raises(HTTPException) as exc_info:
        await update_workflow_version_runnable_config(
            str(workflow_id),
            1,
            request,
            Repository(),
            _MOCK_TENANT,
        )

    assert exc_info.value.status_code == 404
