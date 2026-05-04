"""System metadata routes."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status
from orcheo_backend.app.dependencies import (
    ExternalAgentRuntimeStoreDep,
    PluginInstallationStoreDep,
)
from orcheo_backend.app.external_agent_runtime_store import (
    list_external_agent_providers,
)
from orcheo_backend.app.plugin_inventory import list_runtime_plugins
from orcheo_backend.app.schemas.system import (
    ExternalAgentLoginInputRequest,
    ExternalAgentLoginSession,
    ExternalAgentLoginSessionState,
    ExternalAgentProviderName,
    ExternalAgentProviderState,
    ExternalAgentProviderStatus,
    ExternalAgentsResponse,
    SystemInfoResponse,
    SystemPluginsResponse,
)
from orcheo_backend.app.tenancy import TenantContextDep
from orcheo_backend.app.versioning import get_system_info_payload
from orcheo_backend.worker.celery_app import celery_app


public_router = APIRouter()
router = APIRouter()


@public_router.get("/system/health")
def get_system_health() -> dict[str, str]:
    """Return a lightweight unauthenticated health status."""
    return {"status": "ok"}


@router.get("/system/info", response_model=SystemInfoResponse)
def get_system_info() -> SystemInfoResponse:
    """Return current and latest version metadata for Orcheo components."""
    return SystemInfoResponse.model_validate(get_system_info_payload())


@router.get("/system/plugins", response_model=SystemPluginsResponse)
async def get_system_plugins(
    tenant: TenantContextDep,
    plugin_store: PluginInstallationStoreDep,
) -> SystemPluginsResponse:
    """Return plugin availability with per-tenant overrides."""
    plugins = list_runtime_plugins()
    tenant_states = {
        state.plugin_name: state.enabled
        for state in await plugin_store.list_plugin_states(
            tenant_id=str(tenant.tenant_id)
        )
    }
    for plugin in plugins:
        name = str(plugin["name"])
        if name in tenant_states:
            plugin["enabled"] = tenant_states[name]
    return SystemPluginsResponse.model_validate({"plugins": plugins})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _queue_worker_task(task_name: str, *args: str) -> None:
    celery_app.send_task(task_name, args=list(args))


@router.get("/system/external-agents", response_model=ExternalAgentsResponse)
def get_external_agents(
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentsResponse:
    """Return worker-scoped status for the managed external agent providers."""
    return ExternalAgentsResponse(
        providers=runtime_store.list_provider_statuses(),
    )


@router.post("/system/external-agents/refresh", response_model=ExternalAgentsResponse)
def refresh_external_agents(
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentsResponse:
    """Queue a worker-side status refresh for all external agent providers."""
    for provider_name in list_external_agent_providers():
        current = runtime_store.get_provider_status(provider_name)
        runtime_store.save_provider_status(
            current.model_copy(
                update={
                    "state": ExternalAgentProviderState.CHECKING,
                    "detail": "Checking worker runtime and login state.",
                    "checked_at": _utcnow(),
                }
            )
        )
        _queue_worker_task(
            "orcheo_backend.worker.tasks.refresh_external_agent_status",
            provider_name.value,
        )
    return ExternalAgentsResponse(
        providers=runtime_store.list_provider_statuses(),
    )


@router.post(
    "/system/external-agents/{provider_name}/login",
    response_model=ExternalAgentLoginSession,
)
def start_external_agent_login(
    provider_name: ExternalAgentProviderName,
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentLoginSession:
    """Queue a worker-side OAuth login session for one provider."""
    current = runtime_store.get_provider_status(provider_name)

    now = _utcnow()
    session = ExternalAgentLoginSession(
        session_id=str(uuid4()),
        provider=provider_name,
        display_name=current.display_name,
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
        detail="Preparing the worker-side OAuth flow.",
        resolved_version=current.resolved_version,
        executable_path=current.executable_path,
    )
    runtime_store.save_login_session(session)
    runtime_store.save_provider_status(
        current.model_copy(
            update={
                "state": ExternalAgentProviderState.AUTHENTICATING,
                "detail": "Starting the OAuth flow on the worker.",
                "active_session_id": session.session_id,
            }
        )
    )
    _queue_worker_task(
        "orcheo_backend.worker.tasks.start_external_agent_login",
        provider_name.value,
        session.session_id,
    )
    return session


@router.post(
    "/system/external-agents/{provider_name}/disconnect",
    response_model=ExternalAgentProviderStatus,
)
def disconnect_external_agent(
    provider_name: ExternalAgentProviderName,
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentProviderStatus:
    """Queue worker-side logout and auth cleanup for one provider."""
    current = runtime_store.get_provider_status(provider_name)
    updated = current.model_copy(
        update={
            "state": ExternalAgentProviderState.CHECKING,
            "authenticated": False,
            "detail": "Disconnecting worker auth state.",
            "checked_at": _utcnow(),
        }
    )
    runtime_store.save_provider_status(updated)
    _queue_worker_task(
        "orcheo_backend.worker.tasks.disconnect_external_agent",
        provider_name.value,
    )
    return updated


@router.get(
    "/system/external-agents/sessions/{session_id}",
    response_model=ExternalAgentLoginSession,
)
def get_external_agent_login_session(
    session_id: str,
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentLoginSession:
    """Return one worker-side external agent login session."""
    session = runtime_store.get_login_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"External agent login session '{session_id}' was not found.",
        )
    return session


@router.post(
    "/system/external-agents/sessions/{session_id}/input",
    response_model=ExternalAgentLoginSession,
)
def submit_external_agent_login_input(
    session_id: str,
    payload: ExternalAgentLoginInputRequest,
    runtime_store: ExternalAgentRuntimeStoreDep,
) -> ExternalAgentLoginSession:
    """Queue operator input for a worker-side external agent login session."""
    session = runtime_store.get_login_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"External agent login session '{session_id}' was not found.",
        )
    if session.state in {
        ExternalAgentLoginSessionState.AUTHENTICATED,
        ExternalAgentLoginSessionState.FAILED,
        ExternalAgentLoginSessionState.TIMED_OUT,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"External agent login session '{session_id}' is already complete."
            ),
        )

    input_text = payload.input_text.strip()
    if not input_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Login input must not be empty.",
        )

    runtime_store.save_login_input(session_id, input_text)
    updated = session.model_copy(
        update={
            "detail": "Auth code submitted to the worker. Waiting for completion.",
            "updated_at": _utcnow(),
        }
    )
    runtime_store.save_login_session(updated)
    return updated


__all__ = ["public_router", "router"]
