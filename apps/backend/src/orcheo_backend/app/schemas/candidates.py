"""Schemas for the candidate colleagues endpoint."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class CandidateItem(BaseModel):
    """A candidate AI colleague sourced from the colleague-candidates repo."""

    id: str
    handle: str
    name: str
    description: str | None = None
    emoji: str | None = None
    subtitle: str | None = None
    script: str = ""
    config: dict[str, Any] | None = None
    entrypoint: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    mermaid: str | None = None
