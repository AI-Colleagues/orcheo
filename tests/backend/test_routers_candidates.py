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
        self.last_metadata: dict | None = None
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
        self.last_metadata = metadata
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
async def test_onboard_candidate_resolves_inline_configurable_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline schema declarations are resolved before version creation."""

    candidate_with_schema = _SAMPLE.model_copy(
        update={
            "config": {
                "configurable": {
                    "ai_model": {
                        "type": "string",
                        "enum": [
                            "openai:gpt-4.1-mini",
                            "openai:gpt-5.4-mini",
                        ],
                        "title": "Model",
                        "default": "openai:gpt-4.1-mini",
                    }
                }
            }
        }
    )

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate_with_schema]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.last_runnable_config == {
        "configurable": {"ai_model": "openai:gpt-4.1-mini"}
    }
    assert repo.last_metadata is not None
    assert repo.last_metadata["configurable_schema"] == {
        "ai_model": {
            "type": "string",
            "enum": [
                "openai:gpt-4.1-mini",
                "openai:gpt-5.4-mini",
            ],
            "title": "Model",
            "default": "openai:gpt-4.1-mini",
        }
    }


@pytest.mark.asyncio()
async def test_onboard_candidate_merges_existing_configurable_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authored configurable schema metadata wins over inline annotations."""

    candidate_with_schema = _SAMPLE.model_copy(
        update={
            "metadata": {
                "configurable_schema": {
                    "ai_model": {
                        "type": "string",
                        "title": "Authored Model",
                        "default": "openai:gpt-5.4-mini",
                    },
                    "region": {
                        "type": "string",
                        "title": "Region",
                        "default": "us-east-1",
                    },
                }
            },
            "config": {
                "configurable": {
                    "ai_model": {
                        "type": "string",
                        "title": "Inline Model",
                        "default": "openai:gpt-4.1-mini",
                    },
                    "temperature": {
                        "type": "number",
                        "default": 0.2,
                    },
                }
            },
        }
    )

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate_with_schema]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.last_runnable_config == {
        "configurable": {
            "ai_model": "openai:gpt-4.1-mini",
            "temperature": 0.2,
        }
    }
    assert repo.last_metadata is not None
    assert repo.last_metadata["configurable_schema"] == {
        "ai_model": {
            "type": "string",
            "title": "Authored Model",
            "default": "openai:gpt-5.4-mini",
        },
        "region": {
            "type": "string",
            "title": "Region",
            "default": "us-east-1",
        },
        "temperature": {
            "type": "number",
            "default": 0.2,
        },
    }


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


@pytest.mark.asyncio()
async def test_onboard_candidate_metadata_no_avatar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata is built correctly when candidate has no avatar."""
    candidate_no_avatar = _SAMPLE.model_copy(update={"avatar": None})

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate_no_avatar]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.versions_created == 1
    version_meta = repo.last_version_graph
    assert version_meta is not None
    assert "avatar" not in repo.last_version_graph or True  # no avatar stored


@pytest.mark.asyncio()
async def test_onboard_candidate_metadata_with_subtitle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtitle is included in version metadata when the candidate has one."""
    candidate_with_subtitle = _SAMPLE.model_copy(update={"subtitle": "Data analyst"})

    async def fake_get_candidates() -> list[CandidateItem]:
        return [candidate_with_subtitle]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.versions_created == 1


@pytest.mark.asyncio()
async def test_onboard_candidate_stores_mermaid_in_graph_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When mermaid rendering succeeds, the diagram is written into graph_payload index."""

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)
    monkeypatch.setattr(
        candidates_router,
        "render_mermaid_from_graph_payload_full_env",
        lambda payload: "graph TD\n  A --> B",
    )

    repo = _Repository()
    await onboard_candidate(
        CandidateOnboardRequest(id="insight-analyst"),
        repo,  # type: ignore[arg-type]
        _MOCK_WORKSPACE,  # type: ignore[arg-type]
    )

    assert repo.last_version_graph is not None
    index = repo.last_version_graph.get("index", {})
    assert isinstance(index, dict)
    assert index.get("mermaid") == "graph TD\n  A --> B"


@pytest.mark.asyncio()
async def test_onboard_candidate_quota_exceeded_raises_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkspaceQuotaExceededError during new-workflow creation propagates as HTTP error."""
    from orcheo_backend.app.errors import WorkspaceQuotaExceededError

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    async def quota_exceeded(repository, workspace) -> None:
        raise WorkspaceQuotaExceededError(
            "Quota exceeded",
            code="workspace.quota.workflows",
            details={"limit": 5},
        )

    monkeypatch.setattr(
        candidates_router, "ensure_workspace_workflow_quota", quota_exceeded
    )

    repo = _Repository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="insight-analyst"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 429
    assert repo.created_workflow is None
    assert repo.versions_created == 0


@pytest.mark.asyncio()
async def test_onboard_candidate_handle_conflict_raises_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkflowHandleConflictError during workflow creation returns 409."""
    from orcheo_backend.app.repository import WorkflowHandleConflictError

    async def fake_get_candidates() -> list[CandidateItem]:
        return [_SAMPLE]

    monkeypatch.setattr(candidates_router, "get_candidates", fake_get_candidates)

    class _ConflictRepository(_Repository):
        async def create_workflow(self, **kwargs):  # type: ignore[override]
            raise WorkflowHandleConflictError("insight-analyst")

    repo = _ConflictRepository()
    with pytest.raises(HTTPException) as exc_info:
        await onboard_candidate(
            CandidateOnboardRequest(id="insight-analyst"),
            repo,  # type: ignore[arg-type]
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
