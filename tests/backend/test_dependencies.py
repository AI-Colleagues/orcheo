"""Tests for the shared dependency wiring in the backend."""

from __future__ import annotations
import pytest
from fastapi import HTTPException
from uuid import UUID, uuid4
from orcheo_backend.app import dependencies
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore


def test_get_repository_initializes_when_missing(monkeypatch) -> None:
    original_ref = dict(dependencies._repository_ref)
    dependencies._repository_ref.clear()

    sentinel = object()

    def stub_create_repository(settings=None) -> object:
        dependencies._repository_ref["repository"] = sentinel
        return sentinel

    monkeypatch.setattr(dependencies, "_create_repository", stub_create_repository)

    try:
        repo = dependencies.get_repository()
        assert repo is sentinel
        assert dependencies._repository_ref["repository"] is sentinel
    finally:
        dependencies._repository_ref.clear()
        dependencies._repository_ref.update(original_ref)


def test_set_external_agent_runtime_store_overrides_and_resets() -> None:
    original_store = dependencies._external_agent_runtime_store_ref["store"]
    try:
        store = ExternalAgentRuntimeStore()
        dependencies.set_external_agent_runtime_store(store)
        assert dependencies.get_external_agent_runtime_store() is store

        dependencies.set_external_agent_runtime_store(None)
        renewed = dependencies.get_external_agent_runtime_store()
        assert isinstance(renewed, ExternalAgentRuntimeStore)
        assert renewed is not store
    finally:
        dependencies._external_agent_runtime_store_ref["store"] = original_store


def test_get_history_store_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(dependencies._history_store_ref, "store", None)

    with pytest.raises(RuntimeError, match="History store has not been initialized"):
        dependencies.get_history_store()


def test_get_checkpoint_store_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(dependencies._checkpoint_store_ref, "store", None)

    with pytest.raises(RuntimeError, match="Checkpoint store has not been initialized"):
        dependencies.get_checkpoint_store()


def test_get_plugin_installation_store_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(dependencies._plugin_installation_store_ref, "store", None)

    with pytest.raises(
        RuntimeError,
        match="Plugin installation store has not been initialized",
    ):
        dependencies.get_plugin_installation_store()


def test_get_checkpoint_store_returns_current_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object()
    monkeypatch.setitem(dependencies._checkpoint_store_ref, "store", store)

    assert dependencies.get_checkpoint_store() is store


def test_get_plugin_installation_store_returns_current_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = object()
    monkeypatch.setitem(dependencies._plugin_installation_store_ref, "store", store)

    assert dependencies.get_plugin_installation_store() is store


@pytest.mark.asyncio
async def test_resolve_workflow_workspace_id_returns_none_without_identifiers() -> None:
    class Repository:
        async def get_workflow_workspace_id(self, workflow_id: object) -> object:
            raise AssertionError("repository should not be consulted")

    assert await dependencies.resolve_workflow_workspace_id(Repository(), None) is None


@pytest.mark.asyncio
async def test_resolve_workflow_ref_id_uses_repository_result() -> None:
    expected = UUID("12345678-1234-1234-1234-123456789abc")

    class Repository:
        async def resolve_workflow_ref(
            self,
            workflow_ref: str,
            *,
            include_archived: bool = True,
            workspace_id: str | None = None,
        ) -> UUID:
            assert workflow_ref == "flow-1"
            assert include_archived is False
            assert workspace_id == "workspace-1"
            return expected

    result = await dependencies.resolve_workflow_ref_id(
        Repository(),
        "flow-1",
        include_archived=False,
        workspace_id="workspace-1",
    )

    assert result is expected


@pytest.mark.asyncio
async def test_resolve_workflow_ref_id_translates_not_found() -> None:
    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref: str, **kwargs: object
        ) -> UUID:
            raise dependencies.WorkflowNotFoundError(workflow_ref)

    with pytest.raises(HTTPException, match="Workflow not found"):
        await dependencies.resolve_workflow_ref_id(Repository(), "missing")


@pytest.mark.asyncio
async def test_resolve_optional_workflow_ref_id_handles_none() -> None:
    assert await dependencies.resolve_optional_workflow_ref_id(object(), None) is None


@pytest.mark.asyncio
async def test_resolve_optional_workflow_ref_id_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = uuid4()

    async def fake_resolve_workflow_ref_id(
        repository: object,
        workflow_ref: str,
    ) -> UUID:
        assert workflow_ref == "flow-2"
        return expected

    monkeypatch.setattr(
        dependencies,
        "resolve_workflow_ref_id",
        fake_resolve_workflow_ref_id,
    )

    result = await dependencies.resolve_optional_workflow_ref_id(object(), "flow-2")

    assert result is expected
