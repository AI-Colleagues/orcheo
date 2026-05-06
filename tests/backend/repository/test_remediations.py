from __future__ import annotations
from uuid import UUID, uuid4
import pytest
from orcheo.models.workflow import (
    WorkflowDraftAccess,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
)
from orcheo_backend.app.repository import (
    WorkflowRepository,
    WorkflowRunRemediationNotFoundError,
)


async def _seed_failed_run(repository: WorkflowRepository) -> tuple[UUID, UUID, UUID]:
    workflow = await repository.create_workflow(
        name="Remediation target",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    version = await repository.create_version(
        workflow.id,
        graph={"nodes": [{"id": "start"}], "edges": []},
        metadata={"graph_format": "python"},
        runnable_config={"tags": ["stored"]},
        notes=None,
        created_by="owner",
    )
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="worker",
        input_payload={"secret": "[[vault_key]]"},
    )
    await repository.mark_run_started(run.id, actor="worker")
    await repository.mark_run_failed(run.id, actor="worker", error="boom")
    return workflow.id, version.id, run.id


@pytest.mark.asyncio()
async def test_remediation_candidate_lifecycle(
    repository: WorkflowRepository,
) -> None:
    workflow_id, version_id, run_id = await _seed_failed_run(repository)

    candidate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="version:type:normalized-message",
        version_checksum="checksum-1",
        graph_format="python",
        context={"exception_type": "ValueError", "message": "boom"},
    )

    duplicate = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="version:type:normalized-message",
        version_checksum="checksum-1",
        graph_format="python",
        context={"exception_type": "ValueError", "message": "again"},
    )

    assert duplicate.id == candidate.id
    assert duplicate.context == {"exception_type": "ValueError", "message": "boom"}

    claimed = await repository.claim_next_remediation_candidate(
        actor="worker-1",
    )
    assert claimed is not None
    assert claimed.id == candidate.id
    assert claimed.status is WorkflowRunRemediationStatus.CLAIMED
    assert claimed.attempt_count == 1
    assert claimed.claimed_by == "worker-1"
    assert claimed.claimed_at is not None

    assert await repository.claim_next_remediation_candidate(actor="worker-2") is None

    fixed = await repository.mark_remediation_fixed(
        candidate.id,
        created_version_id=uuid4(),
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        developer_note="Workflow source fixed.",
        artifacts={"classification_hash": "abc"},
        validation_result={"ok": True},
    )

    assert fixed.status is WorkflowRunRemediationStatus.FIXED
    assert fixed.classification is WorkflowRunRemediationClassification.WORKFLOW_FIXABLE
    assert fixed.developer_note == "Workflow source fixed."
    assert fixed.validation_result == {"ok": True}

    recreated = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="version:type:normalized-message",
        version_checksum="checksum-1",
        graph_format="python",
        context={"exception_type": "ValueError", "message": "after terminal"},
    )

    assert recreated.id != candidate.id


@pytest.mark.asyncio()
async def test_remediation_candidate_filters_attempt_cap_and_dismiss(
    repository: WorkflowRepository,
) -> None:
    workflow_id, version_id, run_id = await _seed_failed_run(repository)
    first = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="first",
        version_checksum="checksum-1",
        graph_format=None,
        context={},
    )
    second = await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="second",
        version_checksum="checksum-1",
        graph_format=None,
        context={},
    )

    assert (
        await repository.claim_next_remediation_candidate(
            actor="worker",
            max_attempts=0,
        )
        is None
    )

    claimed = await repository.claim_next_remediation_candidate(
        actor="worker",
        max_attempts=1,
    )
    assert claimed is not None
    assert claimed.id == first.id

    failed = await repository.mark_remediation_failed(
        first.id,
        error="agent crashed",
        artifacts={"stdout_hash": "out"},
        validation_result={"ok": False},
    )
    assert failed.status is WorkflowRunRemediationStatus.FAILED
    assert failed.last_error == "agent crashed"

    dismissed = await repository.dismiss_remediation_candidate(
        first.id,
        actor="reviewer",
        reason="manual fix queued",
    )
    assert dismissed.status is WorkflowRunRemediationStatus.DISMISSED

    pending = await repository.list_remediation_candidates(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        status=WorkflowRunRemediationStatus.PENDING,
    )
    assert [item.id for item in pending] == [second.id]

    limited = await repository.list_remediation_candidates(
        workflow_id=workflow_id,
        limit=1,
    )
    assert len(limited) == 1

    claimed_second = await repository.claim_next_remediation_candidate(
        actor="reviewer",
    )
    assert claimed_second is not None
    assert claimed_second.id == second.id

    note_only = await repository.mark_remediation_note_only(
        second.id,
        classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
        developer_note="Runtime failure needs operator review.",
        artifacts={"classification_hash": "note"},
    )
    assert note_only.status is WorkflowRunRemediationStatus.NOTE_ONLY
    assert note_only.validation_result is None

    with pytest.raises(WorkflowRunRemediationNotFoundError):
        await repository.get_remediation_candidate(uuid4())


@pytest.mark.asyncio()
async def test_repository_reset_clears_remediations(
    repository: WorkflowRepository,
) -> None:
    workflow_id, version_id, run_id = await _seed_failed_run(repository)
    await repository.create_remediation_candidate(
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_id=run_id,
        fingerprint="reset",
        version_checksum="checksum-1",
        graph_format="python",
        context={},
    )

    assert await repository.list_remediation_candidates(workflow_id=workflow_id)

    await repository.reset()

    assert await repository.list_remediation_candidates(workflow_id=workflow_id) == []
