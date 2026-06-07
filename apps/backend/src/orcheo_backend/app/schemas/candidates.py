"""Schemas for the candidate colleagues endpoint."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class CandidateItem(BaseModel):
    """A candidate AI colleague sourced from the colleague-candidates repo.

    This is the internal model; it carries the full workflow script so the
    server-side onboarding endpoint can ingest it without trusting the client.
    """

    id: str
    handle: str
    name: str
    description: str | None = None
    avatar: str | None = None
    subtitle: str | None = None
    script: str = ""
    config: dict[str, Any] | None = None
    entrypoint: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    mermaid: str | None = None


class CandidatePublicItem(BaseModel):
    """Public-facing candidate shape returned by ``GET /candidates``.

    Script, entrypoint, and config are intentionally omitted so clients cannot
    re-post them to the ingestion endpoint.  Onboarding is done server-side via
    ``POST /candidates/onboard``.
    """

    id: str
    handle: str
    name: str
    description: str | None = None
    avatar: str | None = None
    subtitle: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    mermaid: str | None = None
