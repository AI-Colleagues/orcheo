"""Endpoints for managing teams (colleague groupings) within a workspace."""

from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, status
from orcheo_backend.app.dependencies import RepositoryDep
from orcheo_backend.app.repository.errors import TeamSlugConflictError
from orcheo_backend.app.schemas.teams import TeamCreateRequest, TeamItem
from orcheo_backend.app.teams_service import ensure_default_team
from orcheo_backend.app.workspace import WorkspaceContextDep


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/teams", response_model=list[TeamItem])
async def list_teams(
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> list[TeamItem]:
    """Return the teams in the active workspace, default team first.

    The default team is provisioned lazily so a fresh workspace always has at
    least one team to group colleagues under.
    """
    await ensure_default_team(repository, workspace)
    teams = await repository.list_teams(workspace_id=str(workspace.workspace_id))
    return [TeamItem.from_team(team) for team in teams]


@router.post(
    "/teams",
    response_model=TeamItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    request: TeamCreateRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> TeamItem:
    """Create a new team within the active workspace."""
    # Guarantee the default team exists before adding sibling teams.
    await ensure_default_team(repository, workspace)
    try:
        team = await repository.create_team(
            workspace_id=str(workspace.workspace_id),
            name=request.name,
            slug=request.slug,
        )
    except TeamSlugConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "code": "team.slug.conflict"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc), "code": "team.invalid"},
        ) from exc
    return TeamItem.from_team(team)
