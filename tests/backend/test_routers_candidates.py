"""Tests for the /candidates HTTP router."""

from __future__ import annotations
import pytest
from fastapi import HTTPException
from orcheo_backend.app.candidates_service import CandidateFetchError
from orcheo_backend.app.routers import candidates as candidates_router
from orcheo_backend.app.routers.candidates import list_candidates
from orcheo_backend.app.schemas.candidates import CandidateItem


_SAMPLE = CandidateItem(
    id="test_agent",
    handle="test_agent",
    name="Test Agent",
    description="A test agent.",
)


@pytest.mark.asyncio()
async def test_list_candidates_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_candidates returns the items provided by get_candidates."""

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    result = await list_candidates()

    assert len(result) == 1
    assert result[0].id == "test_agent"


@pytest.mark.asyncio()
async def test_list_candidates_raises_502_on_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CandidateFetchError from get_candidates is converted to HTTP 502."""

    async def fail_get_candidates() -> list[CandidateItem]:
        raise CandidateFetchError("cannot reach GitHub")

    monkeypatch.setattr(candidates_router, "get_candidates", fail_get_candidates)

    with pytest.raises(HTTPException) as exc_info:
        await list_candidates()

    assert exc_info.value.status_code == 502
