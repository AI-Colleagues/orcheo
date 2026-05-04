"""Shared dependency wiring for the FastAPI application."""

from __future__ import annotations
from typing import Annotated, cast
from uuid import UUID
from fastapi import Depends, Query
from orcheo.agentensor.checkpoints import AgentensorCheckpointStore
from orcheo.models import CredentialAccessContext
from orcheo.vault import BaseCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.agentensor.checkpoint_store import (
    InMemoryAgentensorCheckpointStore,
)
from orcheo_backend.app.errors import raise_not_found
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore
from orcheo_backend.app.history import InMemoryRunHistoryStore, RunHistoryStore
from orcheo_backend.app.listener_runtime import ListenerRuntimeStore
from orcheo_backend.app.plugin_installation_store import (
    InMemoryPluginInstallationStore,
    PluginInstallationStore,
)
from orcheo_backend.app.providers import (
    create_repository,
    create_vault,
    ensure_credential_service,
)
from orcheo_backend.app.repository import WorkflowNotFoundError, WorkflowRepository


_repository_ref: dict[str, WorkflowRepository] = {}
_history_store_ref: dict[str, RunHistoryStore] = {"store": InMemoryRunHistoryStore()}
_checkpoint_store_ref: dict[str, object] = {
    "store": InMemoryAgentensorCheckpointStore()
}
_listener_runtime_store_ref: dict[str, ListenerRuntimeStore] = {
    "store": ListenerRuntimeStore()
}
_external_agent_runtime_store_ref: dict[str, ExternalAgentRuntimeStore | None] = {
    "store": None
}
_credential_service_ref: dict[str, OAuthCredentialService | None] = {"service": None}
_vault_ref: dict[str, BaseCredentialVault | None] = {"vault": None}
_plugin_installation_store_ref: dict[str, PluginInstallationStore] = {
    "store": InMemoryPluginInstallationStore()
}


def _create_vault(settings: object) -> BaseCredentialVault:
    return create_vault(settings)


def _ensure_vault() -> BaseCredentialVault:
    vault = _vault_ref["vault"]
    if vault is not None:
        return vault
    from orcheo_backend.app import get_settings as _get_settings

    settings = _get_settings()
    vault = _create_vault(settings)
    _vault_ref["vault"] = vault
    return vault


def _ensure_credential_service(
    settings: object | None = None,
) -> OAuthCredentialService:
    service = _credential_service_ref["service"]
    if service is not None:
        return service

    if settings is None:
        from orcheo_backend.app import get_settings as _get_settings

        dynaconf = _get_settings()
    else:
        dynaconf = settings
    vault = _ensure_vault()
    service = ensure_credential_service(dynaconf, vault)  # type: ignore[arg-type]
    _credential_service_ref["service"] = service
    return service


def _create_repository(settings: object | None = None) -> WorkflowRepository:
    if settings is None:
        from orcheo_backend.app import get_settings as _get_settings

        dynaconf = _get_settings()
    else:
        dynaconf = settings
    credential_service = _ensure_credential_service(dynaconf)
    repository = create_repository(
        dynaconf,  # type: ignore[arg-type]
        credential_service,
        _history_store_ref,
        _checkpoint_store_ref,
        _plugin_installation_store_ref,  # type: ignore[arg-type]
    )
    _repository_ref["repository"] = repository
    return repository


def _get_repository() -> WorkflowRepository:
    repository = _repository_ref.get("repository")
    if repository is None:
        repository = _create_repository()
    return repository


def get_repository() -> WorkflowRepository:
    """Return the workflow repository singleton."""
    return _get_repository()


RepositoryDep = Annotated[WorkflowRepository, Depends(get_repository)]


def set_repository(repository: WorkflowRepository) -> None:
    """Override the repository singleton (primarily for testing)."""
    _repository_ref["repository"] = repository


def get_history_store() -> RunHistoryStore:
    """Return the execution history store singleton."""
    return _history_store_ref["store"]


HistoryStoreDep = Annotated[RunHistoryStore, Depends(get_history_store)]


def set_history_store(store: RunHistoryStore) -> None:
    """Override the history store singleton (primarily for testing)."""
    _history_store_ref["store"] = store
    if store is None:  # pragma: no cover - defensive
        _history_store_ref["store"] = InMemoryRunHistoryStore()


def get_checkpoint_store() -> AgentensorCheckpointStore:
    """Return the checkpoint store singleton."""
    return cast(AgentensorCheckpointStore, _checkpoint_store_ref["store"])


CheckpointStoreDep = Annotated[AgentensorCheckpointStore, Depends(get_checkpoint_store)]


def set_checkpoint_store(store: AgentensorCheckpointStore | None) -> None:
    """Override the checkpoint store singleton (primarily for testing)."""
    _checkpoint_store_ref["store"] = cast(
        object, store if store is not None else InMemoryAgentensorCheckpointStore()
    )


def get_listener_runtime_store() -> ListenerRuntimeStore:
    """Return the listener runtime health store singleton."""
    return _listener_runtime_store_ref["store"]


ListenerRuntimeStoreDep = Annotated[
    ListenerRuntimeStore, Depends(get_listener_runtime_store)
]


def set_listener_runtime_store(store: ListenerRuntimeStore | None) -> None:
    """Override the listener runtime store singleton."""
    _listener_runtime_store_ref["store"] = (
        store if store is not None else ListenerRuntimeStore()
    )


def get_external_agent_runtime_store() -> ExternalAgentRuntimeStore:
    """Return the external-agent runtime store singleton."""
    store = _external_agent_runtime_store_ref["store"]
    if store is None:
        store = ExternalAgentRuntimeStore()
        _external_agent_runtime_store_ref["store"] = store
    return store


ExternalAgentRuntimeStoreDep = Annotated[
    ExternalAgentRuntimeStore, Depends(get_external_agent_runtime_store)
]


def set_external_agent_runtime_store(store: ExternalAgentRuntimeStore | None) -> None:
    """Override the external-agent runtime store singleton."""
    _external_agent_runtime_store_ref["store"] = (
        store if store is not None else ExternalAgentRuntimeStore()
    )


def get_credential_service() -> OAuthCredentialService | None:
    """Return the configured credential service if available."""
    return _credential_service_ref["service"]


CredentialServiceDep = Annotated[
    OAuthCredentialService | None, Depends(get_credential_service)
]


def set_credential_service(service: OAuthCredentialService | None) -> None:
    """Override the credential service singleton (primarily for testing)."""
    _credential_service_ref["service"] = service


def get_vault() -> BaseCredentialVault:
    """Return the configured credential vault."""
    vault = _vault_ref["vault"]
    if vault is not None:
        return vault

    return _ensure_vault()


VaultDep = Annotated[BaseCredentialVault, Depends(get_vault)]


def set_vault(vault: BaseCredentialVault | None) -> None:
    """Override the vault singleton (primarily for testing)."""
    _vault_ref["vault"] = vault


def get_plugin_installation_store() -> PluginInstallationStore:
    """Return the plugin installation store singleton."""
    return _plugin_installation_store_ref["store"]


PluginInstallationStoreDep = Annotated[
    PluginInstallationStore, Depends(get_plugin_installation_store)
]


def set_plugin_installation_store(store: PluginInstallationStore | None) -> None:
    """Override the plugin installation store singleton (primarily for testing)."""
    _plugin_installation_store_ref["store"] = (
        store if store is not None else InMemoryPluginInstallationStore()
    )


WorkflowIdQuery = Annotated[UUID | None, Query()]
WorkflowRefQuery = Annotated[str | None, Query(alias="workflow_id")]
IncludeAcknowledgedQuery = Annotated[bool, Query()]


def credential_context_from_workflow(
    workflow_id: UUID | None,
) -> CredentialAccessContext | None:
    """Return a credential context for the provided workflow identifier."""
    if workflow_id is None:
        return None
    return CredentialAccessContext(workflow_id=workflow_id)


async def resolve_workflow_ref_id(
    repository: WorkflowRepository,
    workflow_ref: str,
    *,
    include_archived: bool = True,
    tenant_id: str | None = None,
) -> UUID:
    """Resolve a user-facing workflow ref to the canonical UUID."""
    try:
        return await repository.resolve_workflow_ref(
            workflow_ref,
            include_archived=include_archived,
            tenant_id=tenant_id,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


async def resolve_optional_workflow_ref_id(
    repository: WorkflowRepository,
    workflow_ref: str | None,
) -> UUID | None:
    """Resolve an optional workflow ref to the canonical UUID."""
    if workflow_ref is None:
        return None
    return await resolve_workflow_ref_id(repository, workflow_ref)


__all__ = [
    "CheckpointStoreDep",
    "CredentialServiceDep",
    "ExternalAgentRuntimeStoreDep",
    "HistoryStoreDep",
    "IncludeAcknowledgedQuery",
    "ListenerRuntimeStoreDep",
    "PluginInstallationStoreDep",
    "RepositoryDep",
    "VaultDep",
    "WorkflowIdQuery",
    "WorkflowRefQuery",
    "credential_context_from_workflow",
    "get_checkpoint_store",
    "get_credential_service",
    "get_external_agent_runtime_store",
    "get_history_store",
    "get_listener_runtime_store",
    "get_plugin_installation_store",
    "get_repository",
    "get_vault",
    "resolve_optional_workflow_ref_id",
    "resolve_workflow_ref_id",
    "set_checkpoint_store",
    "set_credential_service",
    "set_external_agent_runtime_store",
    "set_history_store",
    "set_listener_runtime_store",
    "set_plugin_installation_store",
    "set_repository",
    "set_vault",
]
