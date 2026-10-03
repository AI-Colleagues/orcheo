"""MCP controls for the workspace's hosted web apps."""

from __future__ import annotations
import base64
import binascii
from typing import Annotated, Any
from uuid import UUID
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    WRITE,
    WorkspaceArg,
    api_client,
)
from orcheo_backend.app.mcp_server.scopes import requires
from orcheo_backend.app.schemas.apps import (
    AppBindingRequest,
    AppCollectionRequest,
    AppCreateRequest,
    AppPublishRequest,
    AppUpdateRequest,
)


# Bound inline MCP messages; the backend also enforces its configured ZIP limits.
_MAX_BUNDLE_BYTES = 10 * 1024 * 1024
_MAX_BASE64_LENGTH = 4 * ((_MAX_BUNDLE_BYTES + 2) // 3)


def register_hosted_app_tools(server: FastMCP) -> None:
    """Register hosted-app tools with separate read, write and publish grants."""
    _register_app_metadata(server)
    _register_app_bindings(server)
    _register_app_collections(server)
    _register_app_deployments(server)


def _register_app_metadata(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("apps:read"))
    async def list_hosted_apps(
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List hosted apps in the workspace, following next_cursor for more."""
        async with api_client(workspace) as api:
            return await api.get("/api/apps", params={"cursor": cursor, "limit": limit})

    @server.tool(annotations=READ_ONLY, tags=requires("apps:read"))
    async def get_hosted_app(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read app metadata, permission revision and active deployment."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/apps/{app_id}")

    @server.tool(annotations=WRITE, tags=requires("apps:write"))
    async def create_hosted_app(
        config: AppCreateRequest,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Create a draft app and reserve its alias."""
        async with api_client(workspace) as api:
            return await api.post("/api/apps", json_body=config.model_dump(mode="json"))

    @server.tool(annotations=IDEMPOTENT_WRITE, tags=requires("apps:write"))
    async def update_hosted_app(
        app_id: UUID,
        changes: AppUpdateRequest,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Update draft metadata; visibility changes require workspace admin."""
        async with api_client(workspace) as api:
            return await api.request(
                "PATCH",
                f"/api/apps/{app_id}",
                json_body=changes.model_dump(mode="json", exclude_unset=True),
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("apps:write", "apps:publish"))
    async def archive_hosted_app(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Archive an app, disabling access while retaining release history."""
        async with api_client(workspace) as api:
            return await api.post(f"/api/apps/{app_id}/archive")

    @server.tool(
        annotations=IDEMPOTENT_WRITE, tags=requires("apps:write", "apps:publish")
    )
    async def restore_hosted_app(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Restore prior publication state, potentially reenabling public access."""
        async with api_client(workspace) as api:
            return await api.post(f"/api/apps/{app_id}/restore")

    @server.tool(annotations=READ_ONLY, tags=requires("apps:read"))
    async def get_hosted_app_audit(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read app mutation history for the selected workspace."""
        async with api_client(workspace) as api:
            return {"events": await api.get(f"/api/apps/{app_id}/audit")}


def _register_app_bindings(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("apps:read", "workflows:read"))
    async def list_hosted_app_bindings(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read draft workflow bindings and their pinned configuration."""
        async with api_client(workspace) as api:
            return {"bindings": await api.get(f"/api/apps/{app_id}/bindings")}

    @server.tool(annotations=WRITE, tags=requires("apps:write", "workflows:read"))
    async def save_hosted_app_binding(
        app_id: UUID,
        binding: AppBindingRequest,
        binding_id: UUID | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Create a draft binding, or replace one by binding_id; publish to apply."""
        async with api_client(workspace) as api:
            path = f"/api/apps/{app_id}/bindings"
            if binding_id is not None:
                return await api.put(
                    f"{path}/{binding_id}", json_body=binding.model_dump(mode="json")
                )
            return await api.post(path, json_body=binding.model_dump(mode="json"))

    @server.tool(annotations=DESTRUCTIVE, tags=requires("apps:write"))
    async def delete_hosted_app_binding(
        app_id: UUID,
        binding_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Remove a draft binding; publish a release to apply the change."""
        async with api_client(workspace) as api:
            await api.delete(f"/api/apps/{app_id}/bindings/{binding_id}")
        return {"status": "deleted", "binding_id": str(binding_id)}


def _register_app_collections(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("apps:read"))
    async def list_hosted_app_collections(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List app-data collection definitions and access rules."""
        async with api_client(workspace) as api:
            return {"collections": await api.get(f"/api/apps/{app_id}/collections")}

    @server.tool(annotations=WRITE, tags=requires("apps:write"))
    async def save_hosted_app_collection(
        app_id: UUID,
        collection: AppCollectionRequest,
        collection_id: UUID | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Create or replace a draft collection definition; publish to apply."""
        async with api_client(workspace) as api:
            path = f"/api/apps/{app_id}/collections"
            if collection_id is not None:
                return await api.put(
                    f"{path}/{collection_id}",
                    json_body=collection.model_dump(mode="json"),
                )
            return await api.post(path, json_body=collection.model_dump(mode="json"))

    @server.tool(annotations=DESTRUCTIVE, tags=requires("apps:write"))
    async def delete_hosted_app_collection(
        app_id: UUID,
        collection_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Remove an app-data collection definition."""
        async with api_client(workspace) as api:
            await api.delete(f"/api/apps/{app_id}/collections/{collection_id}")
        return {"status": "deleted", "collection_id": str(collection_id)}


def _register_app_deployments(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("apps:read"))
    async def list_hosted_app_deployments(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read deployments, validation status and safe validation errors."""
        async with api_client(workspace) as api:
            return {"deployments": await api.get(f"/api/apps/{app_id}/deployments")}

    @server.tool(annotations=WRITE, tags=requires("apps:write"))
    async def upload_hosted_app_deployment(
        app_id: UUID,
        bundle_base64: Annotated[str, Field(max_length=_MAX_BASE64_LENGTH)],
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Upload a base64 prebuilt ZIP (up to 10 MiB); validate without publishing.

        Uses the backend's multipart upload flow for filesystem/Postgres storage.
        The ZIP must have index.html at its root. No server-side paths are read.
        """
        try:
            bundle = base64.b64decode(bundle_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolError("Provide a valid base64 ZIP archive.") from exc
        if not bundle or len(bundle) > _MAX_BUNDLE_BYTES:
            raise ToolError("The ZIP must be nonempty and at most 10 MiB.")
        async with api_client(workspace) as api:
            return await api.upload(
                f"/api/apps/{app_id}/deployments/upload",
                bundle=bundle,
            )

    @server.tool(annotations=WRITE, tags=requires("apps:publish"))
    async def publish_hosted_app(
        app_id: UUID,
        deployment_id: UUID,
        review: AppPublishRequest,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Publish a validated deployment at the explicitly reviewed revision.

        Review app metadata, bindings and collections before acknowledging the
        permission revision. Publication can make the app publicly accessible.
        """
        async with api_client(workspace) as api:
            return await api.post(
                f"/api/apps/{app_id}/deployments/{deployment_id}/publish",
                json_body=review.model_dump(mode="json"),
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("apps:publish"))
    async def unpublish_hosted_app(
        app_id: UUID,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Withdraw the app's published release."""
        async with api_client(workspace) as api:
            return await api.post(f"/api/apps/{app_id}/unpublish")
