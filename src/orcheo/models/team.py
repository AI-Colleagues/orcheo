"""Team model — a logical grouping of onboarded colleagues within a workspace.

A team is *not* an isolation boundary. Credentials, the vault, quotas, and audit
all remain workspace-scoped. A team only groups workflows (AI colleagues) for
display and management, and scopes colleague handle-uniqueness so the same
candidate can be onboarded into more than one team within a workspace.
"""

from __future__ import annotations
from datetime import datetime
from uuid import UUID, uuid4
from pydantic import Field, field_validator
from orcheo.models.base import OrcheoBaseModel, _utcnow


__all__ = ["Team", "normalize_team_slug"]


def normalize_team_slug(value: str) -> str:
    """Normalize a team slug to a stable, URL-safe form."""
    candidate = str(value).strip().lower()
    if not candidate:
        msg = "Team slug must not be empty."
        raise ValueError(msg)
    if not all(ch.isalnum() or ch in {"-", "_"} for ch in candidate):
        msg = (
            "Team slug must contain only alphanumeric characters, "
            "hyphens, or underscores."
        )
        raise ValueError(msg)
    return candidate


class Team(OrcheoBaseModel):
    """A named group of onboarded colleagues scoped to a single workspace."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: str
    slug: str
    name: str
    is_default: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("slug", mode="before")
    @classmethod
    def _coerce_slug(cls, value: object) -> str:
        return normalize_team_slug(str(value))

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, value: object) -> str:
        candidate = str(value).strip()
        if not candidate:
            msg = "Team name must not be empty."
            raise ValueError(msg)
        return candidate
