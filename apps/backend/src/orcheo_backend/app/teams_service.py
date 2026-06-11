"""Helpers for resolving and provisioning workspace teams."""

from __future__ import annotations
from orcheo.models import Team
from orcheo.workspace.models import WorkspaceContext
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.workspace import get_workspace_repository


async def ensure_default_team(
    repository: WorkflowRepository,
    workspace: WorkspaceContext,
) -> Team:
    """Return the workspace's default team, creating it on first access.

    Requirement: a fresh workspace gets a default team named after the workspace
    (name + slug) so users do not have to create one before onboarding.
    """
    workspace_id = str(workspace.workspace_id)
    slug = getattr(workspace, "workspace_slug", None) or workspace_id
    name = slug
    try:
        record = get_workspace_repository().get_workspace(workspace.workspace_id)
        name = record.name
    except Exception:  # noqa: BLE001 - fall back to the slug as the team name
        name = slug
    return await repository.ensure_default_team(
        workspace_id=workspace_id,
        name=name,
        slug=slug,
    )


__all__ = ["ensure_default_team"]
