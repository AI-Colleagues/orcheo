from __future__ import annotations
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4
import pytest
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.models.workflow import (
    WorkflowDraftAccess,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
    WorkflowRunStatus,
)
from orcheo_backend.app.history import InMemoryRunHistoryStore
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workflow_remediation import (
    WorkflowAutofixSettings,
    attempt_workflow_remediation_async,
    compute_error_fingerprint,
    create_candidate_for_failed_run,
    evaluate_remediation_idle,
    parse_remediation_artifacts,
    redact_sensitive_values,
    scan_workflow_remediations_async,
)


WORKFLOW_SOURCE = textwrap.dedent(
    """
    from langgraph.graph import StateGraph
    from orcheo.graph.state import State

    def build_graph():
        graph = StateGraph(State)
        graph.add_node("first", lambda state: state)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        return graph
    """
).strip()


async def _seed_repository() -> tuple[
    InMemoryWorkflowRepository,
    Any,
    Any,
]:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Autofix target",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    version = await repository.create_version(
        workflow.id,
        graph={
            "format": LANGGRAPH_SCRIPT_FORMAT,
            "source": WORKFLOW_SOURCE,
            "entrypoint": "build_graph",
        },
        metadata={"kind": "script"},
        runnable_config={"metadata": {"credential_ref": "[[openai]]"}},
        notes=None,
        created_by="owner",
    )
    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="worker",
        input_payload={
            "prompt": "hello",
            "api_key": "sk-test1234567890abcdef",
            "credential_ref": "[[openai]]",
        },
        runnable_config={"metadata": {"request_token": "secret-token-value"}},
    )
    await repository.mark_run_started(run.id, actor="worker")
    await repository.mark_run_failed(run.id, actor="worker", error="boom")
    return repository, version, run


def _write_artifacts(
    workspace: Path,
    *,
    classification: WorkflowRunRemediationClassification,
    action: str,
    workflow_source: str = WORKFLOW_SOURCE,
) -> None:
    (workspace / "classification.json").write_text(
        json.dumps(
            {
                "classification": classification.value,
                "confidence": 0.8,
                "suspected_component": {
                    "kind": "workflow",
                    "name": "first",
                    "evidence": ["unit test"],
                },
                "action": action,
                "summary": "Remediation summary.",
                "requires_human_review": True,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "developer_note.md").write_text(
        "Review the generated remediation.",
        encoding="utf-8",
    )
    (workspace / "validation_report.json").write_text(
        json.dumps({"commands": ["pytest"], "ok": True}),
        encoding="utf-8",
    )
    (workspace / "workflow.py").write_text(workflow_source, encoding="utf-8")


def test_redaction_preserves_placeholders_and_removes_real_secrets() -> None:
    value = {
        "api_key": "sk-live1234567890abcdef",
        "credential_ref": "[[openai]]",
        "headers": {"Authorization": "Bearer abc123def456ghi789"},
        "private": ("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"),
        "payload": "token=abc123def456ghi789 and id 123",
        "jwt": "aaaaabbbbbcccccdddddeeeeefffff.aaaaabbbbbcccc.11111222223333",
    }

    redacted = redact_sensitive_values(value)

    assert redacted["api_key"] == "[[REDACTED]]"
    assert redacted["credential_ref"] == "[[openai]]"
    assert redacted["headers"]["Authorization"] == "[[REDACTED]]"
    assert redacted["private"] == "[[REDACTED]]"
    assert "abc123def456ghi789" not in redacted["payload"]
    assert redacted["jwt"] == "[[REDACTED]]"


def test_error_fingerprint_normalizes_literal_values() -> None:
    first = compute_error_fingerprint(
        version_checksum="same",
        exception_type="ValueError",
        message="Failed user 123 token sk-live1234567890abcdef",
        phase="execution",
        failed_component="node",
    )
    second = compute_error_fingerprint(
        version_checksum="same",
        exception_type="ValueError",
        message="Failed user 456 token sk-other1234567890abcdef",
        phase="execution",
        failed_component="node",
    )

    assert first == second


@pytest.mark.asyncio()
async def test_create_candidate_for_failed_run_captures_redacted_context() -> None:
    repository, version, run = await _seed_repository()
    history_store = InMemoryRunHistoryStore()
    await history_store.start_run(
        workflow_id=str(version.workflow_id),
        execution_id=str(run.id),
        inputs={"api_key": "sk-history1234567890abcdef"},
    )
    await history_store.append_step(
        str(run.id),
        {
            "node": "first",
            "status": "error",
            "Authorization": "Bearer abc123def456ghi789",
        },
    )

    try:
        raise RuntimeError("failed account 123 with token sk-err1234567890abcdef")
    except RuntimeError as exc:
        candidate = await create_candidate_for_failed_run(
            repository=repository,
            history_store=history_store,
            run=run,
            exc=exc,
        )

    assert candidate is not None
    assert candidate.workflow_id == version.workflow_id
    assert candidate.workflow_version_id == version.id
    assert candidate.graph_format == LANGGRAPH_SCRIPT_FORMAT
    assert candidate.version_checksum == version.compute_checksum()
    assert candidate.context["workflow_source"] == WORKFLOW_SOURCE
    assert candidate.context["failed_component"] == "first"
    assert candidate.context["inputs"]["api_key"] == "[[REDACTED]]"
    assert candidate.context["inputs"]["credential_ref"] == "[[openai]]"
    assert (
        candidate.context["stored_version_runnable_config"]["metadata"][
            "credential_ref"
        ]
        == "[[openai]]"
    )
    assert candidate.context["recent_run_history"]["inputs"]["api_key"] == (
        "[[REDACTED]]"
    )
    assert (
        candidate.context["normalized_error_message"]
        != candidate.context["error_message"]
    )


@pytest.mark.asyncio()
async def test_failure_handler_only_captures_after_failed_run_is_persisted() -> None:
    from orcheo_backend.worker.tasks import _handle_execution_failure

    run = MagicMock()
    run.id = uuid4()
    run.workflow_version_id = uuid4()
    repository = AsyncMock()
    repository.mark_run_failed = AsyncMock(side_effect=RuntimeError("db down"))
    create_candidate = AsyncMock()

    with patch(
        "orcheo_backend.app.dependencies.get_repository", return_value=repository
    ):
        with patch(
            "orcheo_backend.app.workflow_remediation.create_candidate_for_failed_run",
            create_candidate,
        ):
            with patch("orcheo_backend.worker.tasks.logger"):
                result = await _handle_execution_failure(run, RuntimeError("boom"))

    assert result == {"status": "failed", "error": "boom"}
    create_candidate.assert_not_awaited()


class _FakeInspect:
    def active(self) -> dict[str, list[Any]]:
        return {}

    def reserved(self) -> dict[str, list[Any]]:
        return {}


class _FakeCelery:
    control = MagicMock(inspect=MagicMock(return_value=_FakeInspect()))


@pytest.mark.asyncio()
async def test_idle_evaluation_respects_disabled_flag_and_concurrency() -> None:
    repository, _, run = await _seed_repository()
    await repository.create_remediation_candidate(
        workflow_id=(await repository.get_version(run.workflow_version_id)).workflow_id,
        workflow_version_id=run.workflow_version_id,
        run_id=run.id,
        fingerprint="claimed",
        version_checksum="checksum",
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")

    disabled = await evaluate_remediation_idle(
        repository=repository,
        celery_app=_FakeCelery(),
        settings=WorkflowAutofixSettings(enabled=False),
    )
    assert disabled.is_idle is False
    assert disabled.reason == "disabled"

    busy = await evaluate_remediation_idle(
        repository=repository,
        celery_app=_FakeCelery(),
        settings=WorkflowAutofixSettings(enabled=True, max_concurrent_attempts=1),
    )
    assert busy.is_idle is False
    assert busy.reason == "remediation_concurrency_limit"


@pytest.mark.asyncio()
async def test_scan_claims_pending_candidate_when_idle() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="pending",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )

    with patch(
        "orcheo_backend.app.workflow_remediation._host_load_average", return_value=0.0
    ):
        with patch(
            "orcheo_backend.worker.tasks.attempt_workflow_remediation.delay"
        ) as delay:
            result = await scan_workflow_remediations_async(
                repository=repository,
                celery_app=_FakeCelery(),
                settings=WorkflowAutofixSettings(enabled=True),
            )

    assert result == {"status": "claimed", "remediation_id": str(candidate.id)}
    delay.assert_called_once_with(str(candidate.id))
    claimed = await repository.get_remediation_candidate(candidate.id)
    assert claimed.status is WorkflowRunRemediationStatus.CLAIMED


@pytest.mark.asyncio()
async def test_scan_marks_failed_when_enqueue_fails() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="pending-enqueue-fail",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )

    with patch(
        "orcheo_backend.app.workflow_remediation._host_load_average", return_value=0.0
    ):
        with patch(
            "orcheo_backend.worker.tasks.attempt_workflow_remediation.delay",
            side_effect=RuntimeError("redis unavailable"),
        ):
            result = await scan_workflow_remediations_async(
                repository=repository,
                celery_app=_FakeCelery(),
                settings=WorkflowAutofixSettings(enabled=True),
            )

    assert result == {"status": "failed_to_enqueue", "remediation_id": str(candidate.id)}
    failed = await repository.get_remediation_candidate(candidate.id)
    assert failed.status is WorkflowRunRemediationStatus.FAILED
    assert failed.last_error == "Failed to enqueue remediation attempt task."


def test_parse_artifacts_rejects_note_only_version_creation(tmp_path: Path) -> None:
    _write_artifacts(
        tmp_path,
        classification=WorkflowRunRemediationClassification.UNKNOWN,
        action="create_workflow_version",
    )

    with pytest.raises(ValueError, match="Note-only classifications"):
        parse_remediation_artifacts(tmp_path, original_source=WORKFLOW_SOURCE)


def test_parse_artifacts_rejects_fixable_note_only_action(tmp_path: Path) -> None:
    _write_artifacts(
        tmp_path,
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        action="note_only",
    )

    with pytest.raises(ValueError, match="Workflow-fix classifications"):
        parse_remediation_artifacts(tmp_path, original_source=WORKFLOW_SOURCE)


def test_parse_artifacts_rejects_unknown_action(tmp_path: Path) -> None:
    _write_artifacts(
        tmp_path,
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        action="rewrite_everything",
    )

    with pytest.raises(ValueError, match="Unsupported remediation action"):
        parse_remediation_artifacts(tmp_path, original_source=WORKFLOW_SOURCE)


def test_parse_artifacts_rejects_unexpected_workspace_files(tmp_path: Path) -> None:
    _write_artifacts(
        tmp_path,
        classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
        action="create_workflow_version",
    )
    (tmp_path / "helper.py").write_text("SECRET = 'not allowed'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unexpected remediation artifact"):
        parse_remediation_artifacts(tmp_path, original_source=WORKFLOW_SOURCE)


@pytest.mark.asyncio()
async def test_attempt_note_only_ignores_changed_source() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="note",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={
            "workflow_source": WORKFLOW_SOURCE,
            "per_run_runnable_config": {"metadata": {"request_id": "run-1"}},
            "stored_version_runnable_config": {"metadata": {"profile": "default"}},
        },
    )
    claimed = await repository.claim_next_remediation_candidate(actor="worker")

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        assert remediation.id == claimed.id
        assert settings.dry_run is False
        per_run_config = json.loads(
            (workspace / "per_run_runnable_config.json").read_text(encoding="utf-8")
        )
        stored_config = json.loads(
            (workspace / "stored_runnable_config.json").read_text(encoding="utf-8")
        )
        assert per_run_config == {"metadata": {"request_id": "run-1"}}
        assert stored_config == {"metadata": {"profile": "default"}}
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
            action="note_only",
            workflow_source=f"{WORKFLOW_SOURCE}\n# ignored change",
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=agent_invoker,
    )

    assert result == {"status": "note_only", "classification": "runtime_or_platform"}
    updated = await repository.get_remediation_candidate(candidate.id)
    assert updated.status is WorkflowRunRemediationStatus.NOTE_ONLY
    assert updated.artifacts["source_change_ignored"] is True


@pytest.mark.asyncio()
async def test_attempt_workflow_fix_rejects_unchanged_source() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="unchanged",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del remediation, settings
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            action="create_workflow_version",
            workflow_source=WORKFLOW_SOURCE,
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=agent_invoker,
    )

    updated = await repository.get_remediation_candidate(candidate.id)
    assert result["status"] == "failed"
    assert "did not change workflow.py" in result["error"]
    assert updated.status is WorkflowRunRemediationStatus.FAILED
    assert updated.validation_result is not None
    assert updated.validation_result["ok"] is False
    assert len(await repository.list_versions(version.workflow_id)) == 1


@pytest.mark.asyncio()
async def test_attempt_workflow_fix_creates_validated_version() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="fix",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")

    fixed_source = WORKFLOW_SOURCE.replace('"first"', '"fixed"')

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del remediation, settings
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            action="create_workflow_version",
            workflow_source=fixed_source,
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=agent_invoker,
    )

    assert result["status"] == "fixed"
    created_version_id = UUID(result["created_version_id"])
    created_version = await repository.get_version(created_version_id)
    assert created_version.version == 2
    assert created_version.graph["format"] == LANGGRAPH_SCRIPT_FORMAT
    assert created_version.metadata["remediation"]["id"] == str(candidate.id)
    assert "Automated remediation via Orcheo Vibe" in (created_version.notes or "")


@pytest.mark.asyncio()
async def test_attempt_workflow_fix_retries_created_version_when_enabled() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="retry",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")
    fixed_source = WORKFLOW_SOURCE.replace('"first"', '"fixed"')

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del remediation, settings
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            action="create_workflow_version",
            workflow_source=fixed_source,
        )
        return {"provider": "fake"}

    with patch("orcheo_backend.worker.tasks.execute_run.delay") as delay:
        result = await attempt_workflow_remediation_async(
            repository=repository,
            remediation_id=candidate.id,
            settings=WorkflowAutofixSettings(
                enabled=True,
                dry_run=False,
                retry_after_fix=True,
            ),
            agent_invoker=agent_invoker,
        )

    created_version_id = UUID(result["created_version_id"])
    retry_run_id = UUID(result["retry_after_fix"]["run_id"])
    retry_run = await repository.get_run(retry_run_id)
    updated = await repository.get_remediation_candidate(candidate.id)

    assert result["status"] == "fixed"
    assert result["retry_after_fix"]["enqueued"] is True
    assert retry_run.workflow_version_id == created_version_id
    assert retry_run.status is WorkflowRunStatus.PENDING
    assert retry_run.input_payload == run.input_payload
    assert retry_run.runnable_config == run.runnable_config
    assert updated.artifacts["retry_after_fix"]["run_id"] == str(retry_run_id)
    delay.assert_called_once_with(str(retry_run_id))


@pytest.mark.asyncio()
async def test_attempt_workflow_fix_records_retry_enqueue_failure() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="retry-enqueue-failed",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")
    fixed_source = WORKFLOW_SOURCE.replace('"first"', '"fixed"')

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del remediation, settings
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            action="create_workflow_version",
            workflow_source=fixed_source,
        )
        return {"provider": "fake"}

    with patch(
        "orcheo_backend.worker.tasks.execute_run.delay",
        side_effect=ConnectionError("Redis unavailable"),
    ):
        result = await attempt_workflow_remediation_async(
            repository=repository,
            remediation_id=candidate.id,
            settings=WorkflowAutofixSettings(
                enabled=True,
                dry_run=False,
                retry_after_fix=True,
            ),
            agent_invoker=agent_invoker,
        )

    updated = await repository.get_remediation_candidate(candidate.id)

    assert result["status"] == "fixed"
    assert result["retry_after_fix"]["enqueued"] is False
    assert "Redis unavailable" in result["retry_after_fix"]["error"]
    assert updated.status is WorkflowRunRemediationStatus.FIXED
    assert updated.artifacts["retry_after_fix"]["enqueued"] is False


@pytest.mark.asyncio()
async def test_attempt_workflow_fix_marks_invalid_source_failed() -> None:
    repository, version, run = await _seed_repository()
    candidate = await repository.create_remediation_candidate(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        run_id=run.id,
        fingerprint="invalid",
        version_checksum=version.compute_checksum(),
        graph_format=LANGGRAPH_SCRIPT_FORMAT,
        context={"workflow_source": WORKFLOW_SOURCE},
    )
    await repository.claim_next_remediation_candidate(actor="worker")

    async def agent_invoker(
        workspace: Path,
        remediation: Any,
        settings: WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        del remediation, settings
        _write_artifacts(
            workspace,
            classification=WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
            action="create_workflow_version",
            workflow_source="definitely not valid python:",
        )
        return {"provider": "fake"}

    result = await attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=WorkflowAutofixSettings(enabled=True, dry_run=False),
        agent_invoker=agent_invoker,
    )

    assert result["status"] == "failed"
    updated = await repository.get_remediation_candidate(candidate.id)
    assert updated.status is WorkflowRunRemediationStatus.FAILED
    assert updated.validation_result is not None
    assert updated.validation_result["ok"] is False
    assert len(await repository.list_versions(version.workflow_id)) == 1
