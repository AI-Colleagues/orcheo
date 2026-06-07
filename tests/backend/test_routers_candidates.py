"""Tests for the /candidates HTTP router."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import Workflow, WorkflowDraftAccess
from orcheo_backend.app.candidates_service import CandidateFetchError
from orcheo_backend.app.routers import candidates as candidates_router
from orcheo_backend.app.routers.candidates import list_candidates, onboard_candidate
from orcheo_backend.app.routers.candidates import CandidateOnboardRequest
from orcheo_backend.app.schemas.candidates import CandidateItem


_SAMPLE = CandidateItem(
    id="insight-analyst",
    handle="insight-analyst",
    name="Insight Analyst",
    description="An analyst agent.",
    avatar="avatar-01",
    script=(
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('run', lambda x: x)"
    ),
    entrypoint="graph",
)

_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


@pytest.fixture(autouse=True)
def _patch_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub workspace quota enforcement for all candidate tests."""

    async def _no_op(repository, workspace) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(candidates_router, "ensure_workspace_workflow_quota", _no_op)


# ---------------------------------------------------------------------------
# GET /candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_candidates_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_candidates returns the items provided by get_candidates."""

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    result = await list_candidates()

    assert len(result) == 1
    assert result[0].id == "insight-analyst"


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


# ---------------------------------------------------------------------------
# POST /candidates/onboard
# ---------------------------------------------------------------------------


def _make_workflow(wf_id: UUID, name: str = "Insight Analyst") -> Workflow:
    return Workflow(
        id=wf_id,
        name=name,
        slug="insight-analyst",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _Repository:
    """Minimal in-memory repository stub for onboard tests."""

    def __init__(
        self,
        existing_workflow: Workflow | None = None,
    ) -> None:
        self._existing = existing_workflow
        self.created_workflow: Workflow | None = None
        self.versions_created: int = 0
        self.last_version_graph: dict | None = None
        self.last_runnable_config: dict | None = None

    async def resolve_workflow_ref(
        self, workflow_ref, *, include_archived=True, workspace_id=None
    ) -> UUID:
        from orcheo_backend.app.repository import WorkflowNotFoundError

        if self._existing is not None and self._existing.handle == workflow_ref:
            return self._existing.id
        raise WorkflowNotFoundError(workflow_ref)

    async def get_workflow(self, workflow_id, *, workspace_id=None) -> Workflow:
        assert self._existing is not None
        return self._existing

    async def create_workflow(
        self,
        *,
        name,
        handle,
        slug,
        description,
        tags,
        draft_access,
        actor,
        workspace_id=None,
    ) -> Workflow:  # noqa: PLR0913
        wf = _make_workflow(uuid4(), name=name)
        self.created_workflow = wf
        return wf

    async def create_version(
        self, wf_id, *, graph, metadata, notes, created_by, runnable_config=None
    ):  # noqa: PLR0913
        from orcheo.models import WorkflowVersion

        self.versions_created += 1
        self.last_version_graph = graph
        self.last_runnable_config = runnable_config
        return WorkflowVersion(
            id=uuid4(),
            workflow_id=wf_id,
            version=self.versions_created,
            graph=graph,
            created_by=created_by,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    async def list_workflows(self, *, workspace_id=None, include_archived=False):
        return []

    async def count_workspace_workflows(self, workspace_id):
        return 0


@pytest.mark.asyncio()
async def test_onboard_candidate_creates_workflow_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Onboarding a new candidate creates a workflow shell and ingests version 1."""

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    request = CandidateOnboardRequest(id="insight-analyst")
    result = await onboard_candidate(request, repo, _MOCK_WORKSPACE)  # type: ignore[arg-type]

    assert repo.created_workflow is not None
    assert result.id == repo.created_workflow.id
    assert repo.versions_created == 1
    assert repo.last_version_graph is not None
    assert repo.last_version_graph.get("format") == "langgraph-script"


@pytest.mark.asyncio()
async def test_onboard_candidate_appends_version_to_existing_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-onboarding an existing handle adds a new version without creating a duplicate workflow."""

    existing_wf = _make_workflow(uuid4())
    existing_wf = existing_wf.model_copy(update={"handle": "insight-analyst"})

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository(existing_workflow=existing_wf)
    request = CandidateOnboardRequest(id="insight-analyst")
    result = await onboard_candidate(request, repo, _MOCK_WORKSPACE)  # type: ignore[arg-type]

    assert repo.created_workflow is None, "should not have created a new workflow"
    assert result.id == existing_wf.id
    assert repo.versions_created == 1


@pytest.mark.asyncio()
async def test_onboard_candidate_passes_runnable_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate config is passed as runnable_config when creating the version."""

    candidate_with_config = _SAMPLE.model_copy(
        update={"config": {"configurable": {"model": "gpt-4o"}}}
    )

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate_with_config]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.last_runnable_config == {"configurable": {"model": "gpt-4o"}}


@pytest.mark.asyncio()
async def test_onboard_candidate_rejects_missing_required_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate onboarding fails before version creation when plugins are missing."""

    candidate = _SAMPLE.model_copy(
        update={
            "metadata": {
                "template": {
                    "requiredPlugins": ["orcheo-plugin-lark-listener"],
                }
            }
        }
    )

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)
    monkeypatch.setattr(
        candidates_router,
        "missing_required_plugins",
        lambda required_plugins: list(required_plugins),
    )

    repo = _Repository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="insight-analyst"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert "orcheo-plugin-lark-listener" in str(exc_info.value.detail)
    assert repo.created_workflow is None
    assert repo.versions_created == 0


@pytest.mark.asyncio()
async def test_onboard_candidate_unknown_id_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id not present in the candidates cache returns 404."""

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="nonexistent-agent"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_onboard_candidate_fetch_error_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CandidateFetchError while onboarding is surfaced as HTTP 502."""

    async def fail_get_candidates() -> list[CandidateItem]:
        raise CandidateFetchError("GitHub unreachable")

    monkeypatch.setattr(candidates_router, "get_candidates", fail_get_candidates)

    repo = _Repository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="insight-analyst"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio()
async def test_onboard_candidate_script_error_raises_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A script that fails ingestion returns 400."""

    bad_candidate = _SAMPLE.model_copy(update={"script": "invalid python {{{"})

    async def fake_get_candidates() -> list[CandidateItem]:
        return [bad_candidate]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="insight-analyst"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
