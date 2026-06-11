"""Team grouping operations for the in-memory repository."""

from __future__ import annotations
from uuid import UUID
from orcheo.models import Team, normalize_team_slug
from orcheo_backend.app.repository.errors import (
    TeamNotEmptyError,
    TeamNotFoundError,
    TeamSlugConflictError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


class TeamCrudMixin(InMemoryRepositoryState):
    """Implements team management helpers."""

    def _sorted_teams_locked(self, workspace_id: str) -> list[Team]:
        teams = [
            team for team in self._teams.values() if team.workspace_id == workspace_id
        ]
        teams.sort(key=lambda team: (not team.is_default, team.slug))
        return [team.model_copy(deep=True) for team in teams]

    async def list_teams(self, *, workspace_id: str) -> list[Team]:
        """Return teams in the workspace, default team first."""
        async with self._lock:
            return self._sorted_teams_locked(workspace_id)

    async def get_team(self, team_id: UUID, *, workspace_id: str) -> Team:
        """Return a single team scoped to the workspace."""
        async with self._lock:
            team = self._teams.get(team_id)
            if team is None or team.workspace_id != workspace_id:
                raise TeamNotFoundError(str(team_id))
            return team.model_copy(deep=True)

    async def get_team_by_slug(self, slug: str, *, workspace_id: str) -> Team:
        """Return a team by slug scoped to the workspace."""
        normalized = normalize_team_slug(slug)
        async with self._lock:
            for team in self._teams.values():
                if team.workspace_id == workspace_id and team.slug == normalized:
                    return team.model_copy(deep=True)
            raise TeamNotFoundError(normalized)

    def _create_team_locked(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
        is_default: bool,
    ) -> Team:
        normalized = normalize_team_slug(slug)
        for team in self._teams.values():
            if team.workspace_id == workspace_id and team.slug == normalized:
                msg = f"Team slug already exists: {normalized}"
                raise TeamSlugConflictError(msg)
        team = Team(
            workspace_id=workspace_id,
            name=name,
            slug=normalized,
            is_default=is_default,
        )
        self._teams[team.id] = team
        if is_default:
            self._default_team_by_workspace[workspace_id] = str(team.id)
        return team

    async def create_team(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
        is_default: bool = False,
    ) -> Team:
        """Persist and return a new team; raises on slug conflict."""
        async with self._lock:
            team = self._create_team_locked(
                workspace_id=workspace_id,
                name=name,
                slug=slug,
                is_default=is_default,
            )
            return team.model_copy(deep=True)

    async def ensure_default_team(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
    ) -> Team:
        """Return the workspace's default team, creating it if absent."""
        async with self._lock:
            existing_id = self._default_team_by_workspace.get(workspace_id)
            if existing_id is not None:
                team = self._teams.get(UUID(existing_id))
                if team is not None:
                    return team.model_copy(deep=True)
            team = self._create_team_locked(
                workspace_id=workspace_id,
                name=name,
                slug=slug,
                is_default=True,
            )
            # The default team became the head of every active handle bucket.
            self._rebuild_handle_indexes_locked()
            return team.model_copy(deep=True)

    async def delete_team(self, team_id: UUID, *, workspace_id: str) -> None:
        """Delete a team; raises TeamNotEmptyError if the team has colleagues."""
        async with self._lock:
            team = self._teams.get(team_id)
            if team is None or team.workspace_id != workspace_id:
                raise TeamNotFoundError(str(team_id))
            team_id_str = str(team_id)
            for workflow in self._workflows.values():
                if (
                    workflow.workspace_id == workspace_id
                    and workflow.team_id == team_id_str
                    and not workflow.is_archived
                ):
                    msg = f"Team '{team.name}' still has colleagues."
                    raise TeamNotEmptyError(msg)
            del self._teams[team_id]
            if self._default_team_by_workspace.get(workspace_id) == team_id_str:
                del self._default_team_by_workspace[workspace_id]


__all__ = ["TeamCrudMixin"]
