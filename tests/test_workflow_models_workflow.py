"""Workflow domain model tests split from the original monolithic suite."""

from __future__ import annotations
from uuid import uuid4
import pytest
from pydantic import ValidationError
from orcheo.models import (
    Workflow,
    WorkflowDraftAccess,
    WorkflowRun,
    WorkflowRunRemediation,
    WorkflowRunRemediationAction,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
    WorkflowRunStatus,
    WorkflowVersion,
)
from orcheo.models.workflow_refs import normalize_workflow_handle


def test_workflow_slug_is_derived_from_name() -> None:
    workflow = Workflow(name="My Sample Flow")

    assert workflow.slug == "my-sample-flow"
    assert workflow.audit_log == []


def test_workflow_record_event_updates_timestamp() -> None:
    workflow = Workflow(name="Demo Flow")
    original_updated_at = workflow.updated_at

    workflow.record_event(actor="alice", action="updated", metadata={"field": "name"})

    assert len(workflow.audit_log) == 1
    assert workflow.updated_at >= original_updated_at


def test_workflow_requires_name_or_slug() -> None:
    with pytest.raises(ValidationError):
        Workflow(name="", slug="")


def test_workflow_slug_validator_requires_identifier() -> None:
    workflow = Workflow.model_construct(name="", slug="")
    with pytest.raises(ValueError):
        workflow._populate_slug()


def test_workflow_name_and_description_are_normalized() -> None:
    workflow = Workflow(name="  Demo Flow  ", description="  Some description  ")

    assert workflow.name == "Demo Flow"
    assert workflow.description == "Some description"


def test_workflow_handle_is_normalized_and_validated() -> None:
    workflow = Workflow(name="Demo Flow", handle="Demo-Flow-01")

    assert workflow.handle == "demo-flow-01"


def test_workflow_handle_rejects_uuid_format() -> None:
    with pytest.raises(ValidationError, match="must not use a UUID format"):
        Workflow(name="Demo Flow", handle="550e8400-e29b-41d4-a716-446655440000")


def test_normalize_workflow_handle_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_workflow_handle("   ")


def test_normalize_workflow_handle_rejects_overlong_value() -> None:
    with pytest.raises(ValueError, match="at most 64 characters"):
        normalize_workflow_handle("a" * 65)


def test_normalize_workflow_handle_rejects_invalid_characters() -> None:
    with pytest.raises(ValueError, match="single hyphens"):
        normalize_workflow_handle("not_valid")


def test_workflow_tag_normalization() -> None:
    workflow = Workflow(name="Tagged", tags=["alpha", " Alpha ", "beta", ""])

    assert workflow.tags == ["alpha", "beta"]


def test_workflow_draft_access_defaults_to_personal() -> None:
    workflow = Workflow(name="Draft Flow")

    assert workflow.draft_access is WorkflowDraftAccess.PERSONAL


def test_workflow_draft_access_backfills_legacy_workspace_tags() -> None:
    workflow = Workflow.model_validate(
        {"name": "Workspace Flow", "tags": ["workspace:team-a"]}
    )

    assert workflow.draft_access is WorkflowDraftAccess.WORKSPACE


def test_workflow_draft_access_supports_authenticated_scope() -> None:
    workflow = Workflow(
        name="Shared Flow",
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
    )

    assert workflow.draft_access is WorkflowDraftAccess.AUTHENTICATED


def test_backfill_validator_ignores_non_mapping_inputs() -> None:
    sentinel = "workspace string"

    assert Workflow._backfill_draft_access(sentinel) is sentinel


def test_backfill_validator_requires_list_tags() -> None:
    payload = {"name": "Simple Flow", "tags": "workspace:team-b"}

    assert Workflow._backfill_draft_access(payload) is payload


def test_workflow_version_checksum_is_deterministic() -> None:
    graph_definition = {"nodes": [{"id": "1", "type": "start"}], "edges": []}
    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph=graph_definition,
        created_by="alice",
    )

    checksum = version.compute_checksum()
    assert checksum == version.compute_checksum()
    version.graph["nodes"].append({"id": "2", "type": "end"})
    assert checksum != version.compute_checksum()


def test_workflow_run_state_transitions_and_audit_trail() -> None:
    run = WorkflowRun(workflow_version_id=uuid4(), triggered_by="cron")

    run.mark_started(actor="scheduler")
    assert run.status is WorkflowRunStatus.RUNNING
    assert run.started_at is not None
    assert run.audit_log[-1].action == "run_started"

    run.mark_succeeded(actor="scheduler", output={"messages": 1})
    assert run.status is WorkflowRunStatus.SUCCEEDED
    assert run.completed_at is not None
    assert run.output_payload == {"messages": 1}
    assert run.audit_log[-1].action == "run_succeeded"

    with pytest.raises(ValueError):
        run.mark_cancelled(actor="scheduler")


def test_workflow_run_invalid_transitions_raise_errors() -> None:
    run = WorkflowRun(workflow_version_id=uuid4(), triggered_by="user")

    with pytest.raises(ValueError):
        run.mark_succeeded(actor="user")

    run.mark_started(actor="user")

    with pytest.raises(ValueError):
        run.mark_started(actor="user")

    run.mark_failed(actor="user", error="boom")

    with pytest.raises(ValueError):
        run.mark_failed(actor="user", error="boom")

    with pytest.raises(ValueError):
        run.mark_cancelled(actor="user")


def test_workflow_run_cancel_records_reason() -> None:
    run = WorkflowRun(workflow_version_id=uuid4(), triggered_by="ops")
    run.mark_started(actor="ops")
    run.mark_cancelled(actor="ops", reason="manual stop")

    assert run.status is WorkflowRunStatus.CANCELLED
    assert run.error == "manual stop"
    assert run.audit_log[-1].metadata == {"reason": "manual stop"}


def test_workflow_run_cancel_without_reason() -> None:
    run = WorkflowRun(workflow_version_id=uuid4(), triggered_by="ops")
    run.mark_started(actor="ops")
    run.mark_cancelled(actor="ops")

    assert run.error is None
    assert run.audit_log[-1].metadata == {}


def test_workflow_run_remediation_lifecycle() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="checksum:type:message",
        version_checksum="abc123",
        context={"error": "boom"},
    )

    assert remediation.status is WorkflowRunRemediationStatus.PENDING
    assert remediation.status.is_active is True
    remediation.claim(actor="worker-1")

    assert remediation.status is WorkflowRunRemediationStatus.CLAIMED
    assert remediation.attempt_count == 1
    assert remediation.claimed_by == "worker-1"
    assert remediation.claimed_at is not None

    remediation.mark_note_only(
        actor="worker-1",
        classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
        developer_note="Runtime issue needs operator review.",
        artifacts={"classification_hash": "hash"},
    )

    assert remediation.status is WorkflowRunRemediationStatus.NOTE_ONLY
    assert remediation.action is WorkflowRunRemediationAction.NOTE_ONLY
    assert remediation.classification is (
        WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM
    )
    assert remediation.developer_note == "Runtime issue needs operator review."
    assert remediation.created_version_id is None
    assert remediation.last_error is None


def test_workflow_run_remediation_claim_rejects_non_pending_state() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="checksum:type:message",
        version_checksum="abc123",
    )

    remediation.claim(actor="worker")

    with pytest.raises(ValueError, match="Only pending remediations can be claimed"):
        remediation.claim(actor="worker")


def test_workflow_run_remediation_fixed_requires_version_classification() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="checksum:type:message",
        version_checksum="abc123",
    )

    with pytest.raises(ValueError, match="workflow-fix classifications"):
        remediation.claim(actor="worker")
        remediation.mark_fixed(
            actor="worker",
            created_version_id=uuid4(),
            classification=WorkflowRunRemediationClassification.UNKNOWN,
            developer_note="not fixable",
            artifacts={},
            validation_result={},
        )

    created_version_id = uuid4()
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="checksum:type:message:2",
        version_checksum="abc124",
    )
    remediation.claim(actor="worker")
    remediation.mark_fixed(
        actor="worker",
        created_version_id=created_version_id,
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        developer_note="Workflow source was repaired.",
        artifacts={"workflow_hash": "hash"},
        validation_result={"ok": True},
    )

    assert remediation.status is WorkflowRunRemediationStatus.FIXED
    assert remediation.action is WorkflowRunRemediationAction.CREATE_WORKFLOW_VERSION
    assert remediation.created_version_id == created_version_id
    assert remediation.validation_result == {"ok": True}


def test_workflow_run_remediation_mark_failed_requires_claimed_state() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="failed-guard",
        version_checksum="abc125",
    )

    with pytest.raises(
        ValueError, match="Only claimed remediations can be marked failed"
    ):
        remediation.mark_failed(actor="worker", error="boom")


def test_workflow_run_remediation_mark_note_only_requires_claimed_state() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="note-only-guard",
        version_checksum="abc126",
    )

    with pytest.raises(
        ValueError, match="Only claimed remediations can be marked note-only"
    ):
        remediation.mark_note_only(
            actor="worker",
            classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
            developer_note="note",
            artifacts={},
        )


def test_workflow_run_remediation_mark_failed_persists_optional_artifacts() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="failed-artifacts",
        version_checksum="abc126",
    )

    remediation.claim(actor="worker")
    remediation.mark_failed(
        actor="worker",
        error="boom",
        artifacts={"trace": "payload"},
        validation_result={"ok": False},
    )

    assert remediation.status is WorkflowRunRemediationStatus.FAILED
    assert remediation.last_error == "boom"
    assert remediation.artifacts == {"trace": "payload"}
    assert remediation.validation_result == {"ok": False}


def test_workflow_run_remediation_dismiss_rules() -> None:
    fixed = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="fixed",
        version_checksum="abc123",
    )
    fixed.claim(actor="worker")
    fixed.mark_fixed(
        actor="worker",
        created_version_id=uuid4(),
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        developer_note="fixed",
        artifacts={},
        validation_result={"ok": True},
    )

    with pytest.raises(ValueError, match="active or failed"):
        fixed.dismiss(actor="reviewer")

    failed = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="failed",
        version_checksum="abc123",
    )
    failed.claim(actor="worker")
    failed.mark_failed(actor="worker", error="agent failed")
    failed.dismiss(actor="reviewer", reason="tracked elsewhere")

    assert failed.status is WorkflowRunRemediationStatus.DISMISSED
    assert failed.audit_log[-1].metadata == {"reason": "tracked elsewhere"}


def test_workflow_run_remediation_terminal_transition_requires_claimed() -> None:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="claimed-transition-guard",
        version_checksum="abc123",
    )
    remediation.claim(actor="worker")
    remediation.dismiss(actor="reviewer", reason="manual decision")

    with pytest.raises(ValueError, match="claimed remediations can be marked fixed"):
        remediation.mark_fixed(
            actor="worker",
            created_version_id=uuid4(),
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            developer_note="should not override dismissal",
            artifacts={},
            validation_result={},
        )


def test_workflow_publish_lifecycle() -> None:
    workflow = Workflow(name="Publish Demo")

    workflow.publish(require_login=True, actor="alice")

    assert workflow.is_public is True
    assert workflow.require_login is True
    assert workflow.published_by == "alice"
    assert workflow.published_at is not None
    assert workflow.audit_log[-1].action == "workflow_published"

    workflow.revoke_publish(actor="carol")
    assert workflow.is_public is False
    assert workflow.require_login is False
    assert workflow.published_at is None
    assert workflow.audit_log[-1].action == "workflow_unpublished"


def test_workflow_publish_invalid_transitions() -> None:
    workflow = Workflow(name="Bad Publish")

    with pytest.raises(ValueError):
        workflow.revoke_publish(actor="alice")

    workflow.publish(require_login=False, actor="alice")

    with pytest.raises(ValueError):
        workflow.publish(require_login=False, actor="alice")
