"""Public endpoint exposing candidate AI colleagues from the candidates repo."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException, status
from orcheo_backend.app.candidates_service import CandidateFetchError, get_candidates
from orcheo_backend.app.schemas.candidates import CandidateItem


router = APIRouter()


@router.get("/candidates", response_model=list[CandidateItem])
async def list_candidates() -> list[CandidateItem]:
    """Return candidate AI colleagues sourced from the candidates repository."""
    try:
        return await get_candidates()
    except CandidateFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to load candidate colleagues from the repository.",
        ) from exc
