"""Tests for the shared dependency wiring in the backend."""

from __future__ import annotations
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
