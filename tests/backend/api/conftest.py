"""Fixtures for backend API end-to-end tests."""

from __future__ import annotations
from collections.abc import Iterator
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import InMemoryCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceMembership,
)
from orcheo.workspace.models import WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.chatkit_tokens import reset_chatkit_token_state
from orcheo_backend.app.history import RunHistoryNotFoundError, RunHistoryRecord
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.listener_runtime import ListenerRuntimeStore
from orcheo_backend.app.workspace import reset_workspace_state, set_workspace_repository
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context
from orcheo_backend.app.dependencies import set_listener_runtime_store
from tests.backend.authentication_test_utils import _install_test_authenticator


class _FakeHistoryStore:
    """Minimal dict-backed history store for API integration tests."""

    def __init__(self) -> None:
        self._records: dict[str, RunHistoryRecord] = {}

    async def start_run(
        self, *, workflow_id: str, execution_id: str, inputs=None, **kwargs
    ) -> RunHistoryRecord:
        record = RunHistoryRecord(
            workflow_id=workflow_id,
            execution_id=execution_id,
            inputs=dict(inputs) if inputs else {},
            runnable_config=dict(kwargs.get("runnable_config") or {}),
            trace_id=kwargs.get("trace_id"),
            trace_started_at=kwargs.get("trace_started_at"),
            trace_last_span_at=kwargs.get("trace_started_at"),
        )
        if record.trace_started_at is None:
            record.trace_started_at = record.started_at
        if record.trace_last_span_at is None:
            record.trace_last_span_at = record.trace_started_at
        self._records[execution_id] = record
        return record.model_copy(deep=True)

    async def append_step(self, execution_id: str, payload) -> object:
        return self._records[execution_id].append_step(dict(payload))

    async def mark_completed(self, execution_id: str) -> RunHistoryRecord:
        self._records[execution_id].mark_completed()
        return self._records[execution_id].model_copy(deep=True)

    async def mark_failed(self, execution_id: str, error: str) -> RunHistoryRecord:
        self._records[execution_id].mark_failed(error)
        return self._records[execution_id].model_copy(deep=True)

    async def mark_cancelled(
        self, execution_id: str, *, reason=None
    ) -> RunHistoryRecord:
        self._records[execution_id].mark_cancelled(reason=reason)
        return self._records[execution_id].model_copy(deep=True)

    async def get_history(self, execution_id: str) -> RunHistoryRecord:
        record = self._records.get(execution_id)
        if record is None:
            raise RunHistoryNotFoundError(f"History not found: {execution_id}")
        return record.model_copy(deep=True)

    async def list_histories(self, workflow_id: str, *, limit=None, workspace_id=None):
        records = [
            r.model_copy(deep=True)
            for r in self._records.values()
            if r.workflow_id == workflow_id
        ]
        return records[:limit] if limit else records

    async def clear(self) -> None:
        self._records.clear()


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Yield a configured API client backed by a fresh repository."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    # Use self_host_unsafe so tests can exercise the Python script ingest path.
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "self_host_unsafe")
    monkeypatch.delenv("ORCHEO_AUTH_SERVICE_TOKENS", raising=False)
    monkeypatch.delenv("CHATKIT_TOKEN_SIGNING_KEY", raising=False)
    monkeypatch.delenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", raising=False)
    reset_authentication_state()
    reset_chatkit_token_state()
    reset_workspace_state()
    set_listener_runtime_store(ListenerRuntimeStore())
    _install_test_authenticator(monkeypatch)

    factory_module = import_module("orcheo_backend.app.factory")
    monkeypatch.setattr(
        factory_module,
        "get_chatkit_server",
        lambda: object(),
    )
    monkeypatch.setattr(
        factory_module,
        "ensure_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        factory_module,
        "cancel_chatkit_cleanup_task",
        AsyncMock(return_value=None),
    )

    workspace_id = uuid4()
    workspace_repo = InMemoryWorkspaceRepository()
    workspace_repo.create_workspace(
        Workspace(id=workspace_id, slug="default", name="Default Workspace")
    )
    workspace_repo.add_membership(
        WorkspaceMembership(
            workspace_id=workspace_id, user_id="anonymous", role=Role.OWNER
        )
    )
    set_workspace_repository(workspace_repo)

    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        workspace_slug="default",
        user_id="anonymous",
        role=Role.OWNER,
    )

    cipher = AesGcmCredentialCipher(key="api-client-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=600, providers={})
    repository = InMemoryWorkflowRepository(credential_service=service)
    app = create_app(
        repository, credential_service=service, history_store=_FakeHistoryStore()
    )
    app.state.vault = vault
    app.state.credential_service = service
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context

    try:
        with TestClient(app) as client:
            yield client
    finally:
        set_listener_runtime_store(ListenerRuntimeStore())
        reset_workspace_state()
