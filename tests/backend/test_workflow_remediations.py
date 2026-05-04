"""Tests for workflow autofix remediation support."""

from __future__ import annotations
import json
import textwrap
from pathlib import Path
from typing import Any
from uuid import UUID
import pytest
from orcheo.graph.ingestion import ingest_langgraph_script
from orcheo.models.workflow import (
    WorkflowDraftAccess,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
)
from orcheo_backend.app.history import InMemoryRunHistoryStore
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.repository_sqlite import SqliteWorkflowRepository
from orcheo_backend.app.workflow_remediation import (
    WorkflowAutofixSettings,
    attempt_workflow_remediation_async,
    compute_error_fingerprint,
    create_candidate_for_failed_run,
    evaluate_remediation_idle,
    parse_remediation_artifacts,
    redact_sensitive_values,
)


SCRIPT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph
    from orcheo.graph.state import State
    from orcheo.nodes.rss import RSSNode

    def build_graph():
        graph = StateGraph(State)
        graph.add_node("rss", RSSNode(name="rss", sources=["https://example.com/feed"]))
        graph.set_entry_point("rss")
        graph.set_finish_point("rss")
        return graph
    """
)


async def _seed_repository(repository: Any) -> tuple[UUID, UUID, UUID]:
    workflow = await repository.create_workflow(
        name="Autofix test",
        slug=None,
        description=None,
        tags=[],
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )
    version = await repository.create_version(
        workflow.id,
        graph=ingest_langgraph_script(SCRIPT, entrypoint="build_graph"),
        metadata={"source": "test"},
        notes=None,
        created_by="tester",
    )
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="test",
        input_payload={"token": "secret-value", "safe": "ok"},
        actor="tester",
    )
    return workflow.id, version.id, run.id


@pytest.mark.asyncio
@pytest.mark.parametrize("repository_factory", [InMemoryWorkflowRepository])
async def test_remediation_repository_lifecycle(repository_factory: Any) -> None:
    repository = repository_factory()
    workflow_id, version_id, run_id = await _seed_repository(repository)

    first = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="fp",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": SCRIPT},
    )
    duplicate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="fp",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": SCRIPT},
    )
    claimed = await repository.claim_next_remediation_candidate(actor="worker")
    assert duplicate.id == first.id
    assert claimed is not None
    assert claimed.status is WorkflowRunRemediationStatus.CLAIMED
    assert claimed.attempt_count == 1

    note_only = await repository.mark_remediation_note_only(
        first.id,
        classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
        developer_note="Platform issue",
        artifacts={"summary": "stored"},
    )
    assert note_only.status is WorkflowRunRemediationStatus.NOTE_ONLY
    assert note_only.developer_note == "Platform issue"

    listed = await repository.list_remediation_candidates(run_id=run_id)
    assert [candidate.id for candidate in listed] == [first.id]


@pytest.mark.asyncio
async def test_sqlite_remediation_repository_lifecycle(tmp_path: Path) -> None:
    repository = SqliteWorkflowRepository(tmp_path / "workflows.sqlite")
    workflow_id, version_id, run_id = await _seed_repository(repository)

    candidate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="sqlite-fp",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": SCRIPT},
    )
    claimed = await repository.claim_next_remediation_candidate(actor="worker")
    assert claimed is not None
    assert claimed.id == candidate.id

    dismissed = await repository.dismiss_remediation_candidate(
        candidate.id,
        actor="tester",
        reason="handled manually",
    )
    assert dismissed.status is WorkflowRunRemediationStatus.DISMISSED


def test_redaction_preserves_vault_placeholders_and_masks_tokens() -> None:
    payload = {
        "api_key": "sk-thisisaverysecretapikeyvalue",
        "password": "[[runtime_password]]",
        "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz1234567890"},
        "message": "token abcdefghijklmnopqrstuvwxyz1234567890",
    }

    redacted = redact_sensitive_values(payload)

    assert redacted["api_key"] == "[[REDACTED]]"
    assert redacted["password"] == "[[runtime_password]]"
    assert redacted["headers"]["Authorization"] == "[[REDACTED]]"
    assert redacted["message"] == "token [[REDACTED]]"


def test_error_fingerprint_normalizes_literals() -> None:
    first = compute_error_fingerprint(
        version_checksum="checksum",
        exception_type="ValueError",
        message="Failed for id 11111111-1111-4111-8111-111111111111 at 42",
        phase="execution",
        failed_component="rss",
    )
    second = compute_error_fingerprint(
        version_checksum="checksum",
        exception_type="ValueError",
        message="Failed for id 22222222-2222-4222-8222-222222222222 at 99",
        phase="execution",
        failed_component="rss",
    )

    assert first == second


@pytest.mark.asyncio
async def test_failed_run_creates_redacted_candidate() -> None:
    repository = InMemoryWorkflowRepository()
    history_store = InMemoryRunHistoryStore()
    workflow_id, _, run_id = await _seed_repository(repository)
    run = await repository.mark_run_started(run_id, actor="tester")
    await history_store.start_run(
        workflow_id=str(workflow_id),
        execution_id=str(run_id),
        inputs={"api_key": "secret"},
    )
    await history_store.append_step(str(run_id), {"rss": {"status": "started"}})
    exc = RuntimeError("boom token abcdefghijklmnopqrstuvwxyz1234567890")

    candidate = await create_candidate_for_failed_run(
        repository=repository,
        history_store=history_store,
        run=run,
        exc=exc,
    )

    assert candidate is not None
    assert candidate.status is WorkflowRunRemediationStatus.PENDING
    assert candidate.context["input_payload"]["token"] == "[[REDACTED]]"
    assert candidate.context["failed_component"] == "rss"
    assert "[[REDACTED]]" in candidate.context["error_message"]


def _write_artifacts(
    workspace: Path,
    *,
    classification: str,
    action: str,
    workflow_source: str = SCRIPT,
) -> None:
    (workspace / "workflow.py").write_text(workflow_source, encoding="utf-8")
    (workspace / "classification.json").write_text(
        json.dumps(
            {
                "classification": classification,
                "confidence": 0.9,
                "suspected_component": {
                    "kind": "workflow",
                    "name": "rss",
                    "evidence": ["traceback"],
                },
                "action": action,
                "summary": "Handled failure",
                "requires_human_review": True,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "developer_note.md").write_text("Review note", encoding="utf-8")
    (workspace / "validation_report.json").write_text(
        json.dumps({"commands": ["orcheo workflow validate"], "ok": True}),
        encoding="utf-8",
    )


def test_parse_rejects_note_only_source_creation(tmp_path: Path) -> None:
    _write_artifacts(
        tmp_path,
        classification="runtime_or_platform",
        action="create_workflow_version",
    )

    with pytest.raises(ValueError, match="Note-only classifications"):
        parse_remediation_artifacts(
            tmp_path,
            original_source=SCRIPT,
            provider_metadata={"provider": "fake"},
        )


@pytest.mark.asyncio
async def test_attempt_note_only_ignores_changed_source() -> None:
    repository = InMemoryWorkflowRepository()
    workflow_id, version_id, run_id = await _seed_repository(repository)
    candidate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="note",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": SCRIPT},
    )
    claimed = await repository.claim_next_remediation_candidate(actor="worker")
    assert claimed is not None

    async def fake_agent(
        workspace: Path,
        candidate: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del candidate, settings
        _write_artifacts(
            workspace,
            classification="runtime_or_platform",
            action="note_only",
            workflow_source=SCRIPT + "\n# ignored\n",
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=fake_agent,
    )

    stored = await repository.get_remediation_candidate(candidate.id)
    versions = await repository.list_versions(workflow_id)
    assert result["status"] == "note_only"
    assert stored.status is WorkflowRunRemediationStatus.NOTE_ONLY
    assert stored.artifacts["source_change_ignored"] is True
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_attempt_workflow_fix_creates_version() -> None:
    repository = InMemoryWorkflowRepository()
    workflow_id, version_id, run_id = await _seed_repository(repository)
    candidate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="fix",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": SCRIPT},
    )
    await repository.claim_next_remediation_candidate(actor="worker")

    async def fake_agent(
        workspace: Path,
        candidate: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del candidate, settings
        _write_artifacts(
            workspace,
            classification="workflow_fixable",
            action="create_workflow_version",
            workflow_source=SCRIPT + "\n# fixed\n",
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=fake_agent,
    )

    stored = await repository.get_remediation_candidate(candidate.id)
    versions = await repository.list_versions(workflow_id)
    assert result["status"] == "fixed"
    assert stored.status is WorkflowRunRemediationStatus.FIXED
    assert stored.created_version_id == versions[-1].id
    assert versions[-1].metadata["remediation"]["id"] == str(candidate.id)


@pytest.mark.asyncio
async def test_idle_decision_skips_disabled() -> None:
    repository = InMemoryWorkflowRepository()

    decision = await evaluate_remediation_idle(
        repository=repository,
        celery_app=object(),
        settings=WorkflowAutofixSettings(enabled=False),
    )

    assert decision.is_idle is False
    assert decision.reason == "disabled"
