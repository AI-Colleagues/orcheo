"""Multi-workspace configuration settings.

Multi-tenant workspaces are always on — there is no enable/disable flag.
These settings only control the default slug used when a request omits the
workspace header and the header name itself.
"""

from __future__ import annotations
from typing import cast
from pydantic import BaseModel, Field, field_validator
from orcheo.config.defaults import _DEFAULTS
from orcheo.workspace.models import normalize_slug


__all__ = ["MultiWorkspaceSettings"]


class MultiWorkspaceSettings(BaseModel):
    """Runtime configuration for multi-workspace request resolution."""

    default_workspace_slug: str = Field(
        default=cast(str, _DEFAULTS["MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG"])
    )
    workspace_header: str = Field(
        default=cast(str, _DEFAULTS["MULTI_WORKSPACE_WORKSPACE_HEADER"])
    )

    @field_validator("default_workspace_slug", mode="before")
    @classmethod
    def _coerce_slug(cls, value: object) -> str:
        if value is None or value == "":
            return cast(str, _DEFAULTS["MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG"])
        return normalize_slug(str(value))

    @field_validator("workspace_header", mode="before")
    @classmethod
    def _coerce_header(cls, value: object) -> str:
        if value is None or value == "":
            return cast(str, _DEFAULTS["MULTI_WORKSPACE_WORKSPACE_HEADER"])
        candidate = str(value).strip()
        if not candidate:
            msg = "Workspace header must not be empty."
            raise ValueError(msg)
        return candidate
