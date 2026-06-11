"""Team grouping operations for the PostgreSQL repository."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from orcheo.models import Team, normalize_team_slug
from orcheo_backend.app.repository.errors import (
    TeamNotEmptyError,
    TeamNotFoundError,
    TeamSlugConflictError,
)
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class TeamRepositoryMixin(PostgresPersistenceMixin):
    """Helpers for managing teams within a workspace."""

    @staticmethod
    def _row_to_team(row: dict[str, Any]) -> Team:
        return Team(
            id=UUID(row["id"]),
            workspace_id=row["workspace_id"],
            slug=row["slug"],
            name=row["name"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_teams(self, *, workspace_id: str) -> list[Team]:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, workspace_id, slug, name, is_default,
                           created_at, updated_at
                      FROM teams
                     WHERE workspace_id = %s
                  ORDER BY is_default DESC, slug ASC
                    """,
                    (workspace_id,),
                )
                rows = await cursor.fetchall()
            return [self._row_to_team(row) for row in rows]

    async def get_team(self, team_id: UUID, *, workspace_id: str) -> Team:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, workspace_id, slug, name, is_default,
                           created_at, updated_at
                      FROM teams
                     WHERE id = %s AND workspace_id = %s
                    """,
                    (str(team_id), workspace_id),
                )
                row = await cursor.fetchone()
        if row is None:
            raise TeamNotFoundError(str(team_id))
        return self._row_to_team(row)

    async def get_team_by_slug(self, slug: str, *, workspace_id: str) -> Team:
        await self._ensure_initialized()
        normalized = normalize_team_slug(slug)
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, workspace_id, slug, name, is_default,
                           created_at, updated_at
                      FROM teams
                     WHERE slug = %s AND workspace_id = %s
                    """,
                    (normalized, workspace_id),
                )
                row = await cursor.fetchone()
        if row is None:
            raise TeamNotFoundError(normalized)
        return self._row_to_team(row)

    async def _insert_team_locked(
        self,
        conn: Any,
        *,
        workspace_id: str,
        name: str,
        slug: str,
        is_default: bool,
    ) -> Team:
        team = Team(
            workspace_id=workspace_id,
            name=name,
            slug=slug,
            is_default=is_default,
        )
        try:
            await conn.execute(
                """
                INSERT INTO teams (
                    id, workspace_id, slug, name, is_default,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(team.id),
                    team.workspace_id,
                    team.slug,
                    team.name,
                    team.is_default,
                    team.created_at,
                    team.updated_at,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - normalize unique-violation
            message = str(exc).lower()
            if "teams_workspace_id_slug_key" in message or "unique" in message:
                raise TeamSlugConflictError(
                    f"Team slug already exists: {team.slug}"
                ) from exc
            raise
        return team

    async def create_team(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
        is_default: bool = False,
    ) -> Team:
        await self._ensure_initialized()
        normalized = normalize_team_slug(slug)
        async with self._lock:
            async with self._connection() as conn:
                return await self._insert_team_locked(
                    conn,
                    workspace_id=workspace_id,
                    name=name,
                    slug=normalized,
                    is_default=is_default,
                )

    async def ensure_default_team(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
    ) -> Team:
        await self._ensure_initialized()
        normalized = normalize_team_slug(slug)
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, workspace_id, slug, name, is_default,
                           created_at, updated_at
                      FROM teams
                     WHERE workspace_id = %s AND is_default = TRUE
                     LIMIT 1
                    """,
                    (workspace_id,),
                )
                row = await cursor.fetchone()
                if row is not None:
                    return self._row_to_team(row)

                team = await self._insert_team_locked(
                    conn,
                    workspace_id=workspace_id,
                    name=name,
                    slug=normalized,
                    is_default=True,
                )
            return team

    async def delete_team(self, team_id: UUID, *, workspace_id: str) -> None:
        """Delete a team; raises TeamNotEmptyError if the team has colleagues."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT name FROM teams WHERE id = %s AND workspace_id = %s",
                    (str(team_id), workspace_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise TeamNotFoundError(str(team_id))
                team_name = row["name"]

                cursor = await conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                      FROM workflows
                     WHERE team_id = %s
                       AND workspace_id = %s
                       AND is_archived = FALSE
                    """,
                    (str(team_id), workspace_id),
                )
                count_row = await cursor.fetchone()
                if count_row and int(count_row["cnt"]) > 0:
                    msg = f"Team '{team_name}' still has colleagues."
                    raise TeamNotEmptyError(msg)

                await conn.execute(
                    "DELETE FROM teams WHERE id = %s AND workspace_id = %s",
                    (str(team_id), workspace_id),
                )


__all__ = ["TeamRepositoryMixin"]
