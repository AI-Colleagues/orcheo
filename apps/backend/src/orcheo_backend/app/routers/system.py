"""System metadata routes."""

from __future__ import annotations
from fastapi import APIRouter
from orcheo_backend.app.dependencies import PluginInstallationStoreDep
from orcheo_backend.app.plugin_inventory import list_runtime_plugins
from orcheo_backend.app.schemas.system import (
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


@router.get("/system/info", response_model=SystemInfoResponse)
def get_system_info() -> SystemInfoResponse:
    """Return current and latest version metadata for Orcheo components."""
    return SystemInfoResponse.model_validate(get_system_info_payload())


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
