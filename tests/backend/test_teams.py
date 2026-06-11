"""Tests for team grouping: repository semantics, endpoints, and onboarding."""

from __future__ import annotations
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    TeamSlugConflictError,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
)
from orcheo_backend.app.routers import candidates as candidates_router
from orcheo_backend.app.routers import teams as teams_router
from orcheo_backend.app.routers.candidates import CandidateOnboardRequest
from orcheo_backend.app.schemas.candidates import CandidateItem


WS = "11111111-1111-1111-1111-111111111111"
WORKSPACE = SimpleNamespace(workspace_id=WS, workspace_slug="acme")


_CANDIDATE = CandidateItem(
    id="insight-analyst",
    handle="insight-analyst",
    name="Insight Analyst",
    description="An analyst agent.",
    script=(
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('run', lambda x: x)"
    ),
    entrypoint="graph",
)


async def _new_colleague(repo, handle, team_id):
    return await repo.create_workflow(
        name=handle,
        handle=handle,
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.WORKSPACE,
        actor="t",
        workspace_id=WS,
        team_id=team_id,
    )


# ---------------------------------------------------------------------------
# Repository semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_team_is_created_once_and_listed_first() -> None:
    repo = InMemoryWorkflowRepository()
    first = await repo.ensure_default_team(workspace_id=WS, name="Acme", slug="acme")
    again = await repo.ensure_default_team(workspace_id=WS, name="Acme", slug="acme")
    assert first.id == again.id
    assert first.is_default is True

    await repo.create_team(workspace_id=WS, name="Sales", slug="sales")
    teams = await repo.list_teams(workspace_id=WS)
    assert [t.slug for t in teams] == ["acme", "sales"]
    assert teams[0].is_default is True


@pytest.mark.asyncio
async def test_same_handle_allowed_across_teams_but_not_within_team() -> None:
    repo = InMemoryWorkflowRepository()
    default = await repo.ensure_default_team(workspace_id=WS, name="Acme", slug="acme")
    sales = await repo.create_team(workspace_id=WS, name="Sales", slug="sales")

    a = await _new_colleague(repo, "bot", str(default.id))
    b = await _new_colleague(repo, "bot", str(sales.id))
    assert a.id != b.id

    with pytest.raises(WorkflowHandleConflictError):
        await _new_colleague(repo, "bot", str(sales.id))


@pytest.mark.asyncio
async def test_bare_handle_resolves_to_default_team() -> None:
    repo = InMemoryWorkflowRepository()
    default = await repo.ensure_default_team(workspace_id=WS, name="Acme", slug="acme")
    sales = await repo.create_team(workspace_id=WS, name="Sales", slug="sales")

    default_wf = await _new_colleague(repo, "bot", str(default.id))
    await _new_colleague(repo, "bot", str(sales.id))

    resolved = await repo.resolve_workflow_ref(
        "bot", workspace_id=WS, include_archived=False
    )
    assert resolved == default_wf.id


@pytest.mark.asyncio
async def test_team_scoped_resolution_misses_other_teams() -> None:
    repo = InMemoryWorkflowRepository()
    default = await repo.ensure_default_team(workspace_id=WS, name="Acme", slug="acme")
    sales = await repo.create_team(workspace_id=WS, name="Sales", slug="sales")
    await _new_colleague(repo, "bot", str(default.id))

    with pytest.raises(WorkflowNotFoundError):
        await repo.resolve_workflow_ref(
            "bot", workspace_id=WS, team_id=str(sales.id), include_archived=False
        )


@pytest.mark.asyncio
async def test_duplicate_team_slug_conflicts() -> None:
    repo = InMemoryWorkflowRepository()
    await repo.create_team(workspace_id=WS, name="Sales", slug="sales")
    with pytest.raises(TeamSlugConflictError):
        await repo.create_team(workspace_id=WS, name="Sales 2", slug="sales")


# ---------------------------------------------------------------------------
# /teams endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_teams_provisions_default_team() -> None:
    repo = InMemoryWorkflowRepository()
    result = await teams_router.list_teams(repo, WORKSPACE)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0].is_default is True
    assert result[0].slug == "acme"


@pytest.mark.asyncio
async def test_create_team_endpoint_then_conflict() -> None:
    repo = InMemoryWorkflowRepository()
    from orcheo_backend.app.schemas.teams import TeamCreateRequest

    created = await teams_router.create_team(
        TeamCreateRequest(name="Sales", slug="sales"),
        repo,
        WORKSPACE,  # type: ignore[arg-type]
    )
    assert created.slug == "sales"
    assert created.is_default is False

    with pytest.raises(HTTPException) as exc:
        await teams_router.create_team(
            TeamCreateRequest(name="Sales", slug="sales"),
            repo,
            WORKSPACE,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Team-aware onboarding
# ---------------------------------------------------------------------------


@pytest.fixture()
def _patch_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_candidates() -> list[CandidateItem]:
        return [_CANDIDATE]

    async def _no_quota(repository, workspace) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)
    monkeypatch.setattr(candidates_router, "ensure_workspace_workflow_quota", _no_quota)


@pytest.mark.asyncio
async def test_onboard_defaults_to_default_team(
    _patch_onboarding: None,
) -> None:
    repo = InMemoryWorkflowRepository()
    request = CandidateOnboardRequest(id="insight-analyst")
    wf = await candidates_router.onboard_candidate(request, repo, WORKSPACE)  # type: ignore[arg-type]

    default = await repo.ensure_default_team(workspace_id=WS, name="acme", slug="acme")
    stored = await repo.get_workflow(wf.id, workspace_id=WS)
    assert stored.team_id == str(default.id)


@pytest.mark.asyncio
async def test_onboard_same_candidate_into_two_teams_creates_two_colleagues(
    _patch_onboarding: None,
) -> None:
    repo = InMemoryWorkflowRepository()
    sales = await repo.create_team(workspace_id=WS, name="Sales", slug="sales")

    default_wf = await candidates_router.onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,
        WORKSPACE,  # type: ignore[arg-type]
    )
    sales_wf = await candidates_router.onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst", team_id=str(sales.id)),
        repo,
        WORKSPACE,  # type: ignore[arg-type]
    )
    assert default_wf.id != sales_wf.id

    workflows = await repo.list_workflows(workspace_id=WS)
    assert len(workflows) == 2


@pytest.mark.asyncio
async def test_reonboard_same_team_bumps_version(_patch_onboarding: None) -> None:
    repo = InMemoryWorkflowRepository()
    first = await candidates_router.onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,
        WORKSPACE,  # type: ignore[arg-type]
    )
    second = await candidates_router.onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,
        WORKSPACE,  # type: ignore[arg-type]
    )
    assert first.id == second.id
    versions = await repo.list_versions(first.id)
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_onboard_into_unknown_team_returns_404(
    _patch_onboarding: None,
) -> None:
    repo = InMemoryWorkflowRepository()
    request = CandidateOnboardRequest(id="insight-analyst", team_id=str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        await candidates_router.onboard_candidate(request, repo, WORKSPACE)  # type: ignore[arg-type]
    assert exc.value.status_code == 404
