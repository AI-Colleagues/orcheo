"""System metadata routes."""

from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException
from redis.asyncio import Redis
from orcheo.graph.ingestion.sandbox import uploads_allowed
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError
from orcheo_backend.app.dependencies import PluginInstallationStoreDep
from orcheo_backend.app.local_execution import inprocess_execution_enabled
from orcheo_backend.app.plugin_inventory import list_runtime_plugins
from orcheo_backend.app.schemas.system import (
    SystemFeaturesResponse,
    SystemInfoResponse,
    SystemPluginsResponse,
)
from orcheo_backend.app.versioning import get_system_info_payload
from orcheo_backend.app.workspace import WorkspaceContextDep


public_router = APIRouter()
router = APIRouter()


@public_router.get("/system/health")
def get_system_health() -> dict[str, str]:
    """Return a lightweight unauthenticated health status."""
    return {"status": "ok"}


@public_router.get("/system/ready")
async def get_system_readiness() -> dict[str, str]:
    """Report whether the backend can reach its configured Redis broker."""
    if inprocess_execution_enabled():
        return {"status": "ok"}
    try:
        redis = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            if not await redis.ping():
                raise ConnectionError("Redis ping failed")
        finally:
            await redis.aclose()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok"}


@router.get("/system/info", response_model=SystemInfoResponse)
def get_system_info() -> SystemInfoResponse:
    """Return current and latest version metadata for Orcheo components."""
    payload = get_system_info_payload()
    payload["uploads_allowed"] = uploads_allowed()
    return SystemInfoResponse.model_validate(payload)


@router.get("/system/features", response_model=SystemFeaturesResponse)
def get_system_features(workspace: WorkspaceContextDep) -> SystemFeaturesResponse:
    """Return features available to the selected workspace."""
    try:
        settings = HostedAppsSettings.from_environment()
    except HostedAppsSettingsError:
        return SystemFeaturesResponse(hosted_apps_enabled=False)
    return SystemFeaturesResponse(
        hosted_apps_enabled=settings.enabled
        and settings.allows_workspace(str(workspace.workspace_id))
    )


@router.get("/system/plugins", response_model=SystemPluginsResponse)
async def get_system_plugins(
    workspace: WorkspaceContextDep,
    plugin_store: PluginInstallationStoreDep,
) -> SystemPluginsResponse:
    """Return plugin availability with per-workspace overrides."""
    plugins = list_runtime_plugins()
    workspace_states = {
        state.plugin_name: state.enabled
        for state in await plugin_store.list_plugin_states(
            workspace_id=str(workspace.workspace_id)
        )
    }
    for plugin in plugins:
        name = str(plugin["name"])
        if name in workspace_states:
            plugin["enabled"] = workspace_states[name]
    return SystemPluginsResponse.model_validate({"plugins": plugins})


__all__ = ["public_router", "router"]
