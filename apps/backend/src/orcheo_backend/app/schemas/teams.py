"""Schemas for the team grouping endpoints."""

from __future__ import annotations
from pydantic import BaseModel, Field
from orcheo.models import Team


class TeamItem(BaseModel):
    """Public representation of a team within a workspace."""

    id: str
    slug: str
    name: str
    is_default: bool

    @classmethod
    def from_team(cls, team: Team) -> TeamItem:
        """Build the public shape from a domain ``Team``."""
        return cls(
            id=str(team.id),
            slug=team.slug,
            name=team.name,
            is_default=team.is_default,
        )


class TeamCreateRequest(BaseModel):
    """Request body for creating a team."""

    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=64)
