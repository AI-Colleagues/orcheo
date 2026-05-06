"""Focused tests for workflow remediation helper branches."""

from __future__ import annotations
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from orcheo.models.workflow import (
    WorkflowRun,
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
    WorkflowRunStatus,
    WorkflowVersion,
)
from orcheo_backend.app import workflow_remediation
from orcheo_backend.app.history import RunHistoryNotFoundError


WORKFLOW_SCRIPT = textwrap.dedent(
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


def _write_artifacts(
    workspace: Path,
    *,
    classification: str,
    action: str,
    workflow_source: str,
    developer_note: str = "Review note",
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
    (workspace / "developer_note.md").write_text(developer_note, encoding="utf-8")
    (workspace / "validation_report.json").write_text(
        json.dumps({"commands": ["orcheo workflow validate"], "ok": True}),
        encoding="utf-8",
    )


def _remediation_candidate(
    *, status: WorkflowRunRemediationStatus
) -> WorkflowRunRemediation:
    candidate = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="fingerprint",
        version_checksum="checksum",
        context={"workflow_source": "source"},
    )
    candidate.status = status
    return candidate


def test_load_workflow_autofix_settings_uses_scalar_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "workflow_autofix.enabled": True,
        "workflow_autofix.max_concurrent_attempts": "0",
        "workflow_autofix.idle_load_threshold": "2.5",
        "workflow_autofix.dry_run": None,
        "workflow_autofix.max_attempts_per_candidate": "not-an-int",
        "workflow_autofix.unknown_load_allows_remediation": False,
        "workflow_autofix.agent_provider": "codex",
        "workflow_autofix.agent_timeout_seconds": 0,
        "workflow_autofix.retry_after_fix": "yes",
    }

    def fake_settings_value(
        _settings: object | None,
        *,
        attr_path: str,
        env_key: str,
        default: Any,
    ) -> Any:
        del env_key, default
        return values[attr_path]

    monkeypatch.setattr(workflow_remediation, "settings_value", fake_settings_value)

    settings = workflow_remediation.load_workflow_autofix_settings(object())

    assert settings.enabled is True
    assert settings.max_concurrent_attempts == 1
    assert settings.idle_load_threshold == 2.5
    assert settings.dry_run is True
    assert settings.max_attempts_per_candidate == 3
    assert settings.unknown_load_allows_remediation is False
    assert settings.agent_provider == "codex"
    assert settings.agent_timeout_seconds == 1
    assert settings.retry_after_fix is True


def test_workflow_autofix_settings_defaults_enabled() -> None:
    settings = workflow_remediation.WorkflowAutofixSettings()

    assert settings.enabled is True


def test_redact_sensitive_values_handles_awaitables_and_tuples() -> None:
    class AwaitableValue:
        def __await__(self):
            if False:  # pragma: no cover - generator protocol
                yield None
            return "secret-value"

    class ModelWithAwaitableDump:
        def model_dump(self, mode: str = "json") -> Any:
            del mode
            return AwaitableValue()

    class ModelWithDictDump:
        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            del mode
            return {"token": "value"}

    class OpaqueValue:
        def __str__(self) -> str:
            return "opaque"

    redacted = workflow_remediation.redact_sensitive_values(
        {
            "tuple": ("alpha", "beta"),
            "set": {"gamma"},
            "model": ModelWithAwaitableDump(),
            "awaitable": AwaitableValue(),
            "mapped_model": ModelWithDictDump(),
            "opaque": OpaqueValue(),
        }
    )

    assert redacted["tuple"] == ["alpha", "beta"]
    assert redacted["set"] == ["gamma"]
    assert isinstance(redacted["model"], str)
    assert isinstance(redacted["awaitable"], str)
    assert redacted["mapped_model"] == {"token": "[[REDACTED]]"}
    assert redacted["opaque"] == "opaque"


@pytest.mark.asyncio
async def test_create_candidate_for_failed_run_returns_none_for_non_models() -> None:
    repository = AsyncMock()
    repository.get_version = AsyncMock(return_value=object())
    repository.get_run = AsyncMock(return_value=object())
    run = SimpleNamespace(id=uuid4(), workflow_version_id=uuid4())

    result = await workflow_remediation.create_candidate_for_failed_run(
        repository=repository,
        history_store=None,
        run=run,  # type: ignore[arg-type]
        exc=RuntimeError("boom"),
    )

    assert result is None


@pytest.mark.asyncio
async def test_create_candidate_for_failed_run_accepts_non_mapping_graph() -> None:
    version = WorkflowVersion.model_construct(
        id=uuid4(),
        workflow_id=uuid4(),
        version=1,
        graph="source-string",
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="tester",
    )
    run = WorkflowRun.model_construct(
        id=uuid4(),
        workflow_version_id=version.id,
        triggered_by="tester",
        input_payload={"token": "secret"},
        runnable_config=None,
        status=WorkflowRunStatus.PENDING,
    )
    repository = AsyncMock()
    repository.get_version = AsyncMock(return_value=version)
    repository.get_run = AsyncMock(return_value=run)

    async def create_candidate(**kwargs: Any) -> WorkflowRunRemediation:
        return WorkflowRunRemediation(
            workflow_id=kwargs["workflow_id"],
            workflow_version_id=kwargs["workflow_version_id"],
            run_id=kwargs["run_id"],
            fingerprint=kwargs["fingerprint"],
            version_checksum=kwargs["version_checksum"],
            graph_format=kwargs["graph_format"],
            context=kwargs["context"],
        )

    repository.create_remediation_candidate = AsyncMock(side_effect=create_candidate)

    result = await workflow_remediation.create_candidate_for_failed_run(
        repository=repository,
        history_store=None,
        run=run,
        exc=RuntimeError("boom"),
    )

    assert result is not None
    assert result.context["workflow_entrypoint"] is None
    assert repository.create_remediation_candidate.await_count == 1


@pytest.mark.asyncio
async def test_create_candidate_for_failed_run_returns_none_on_create_failure() -> None:
    version = WorkflowVersion.model_construct(
        id=uuid4(),
        workflow_id=uuid4(),
        version=1,
        graph={"format": "langgraph_script", "source": "workflow.py"},
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="tester",
    )
    run = WorkflowRun.model_construct(
        id=uuid4(),
        workflow_version_id=version.id,
        triggered_by="tester",
        input_payload={},
        runnable_config=None,
        status=WorkflowRunStatus.PENDING,
    )
    repository = AsyncMock()
    repository.get_version = AsyncMock(return_value=version)
    repository.get_run = AsyncMock(return_value=run)
    repository.create_remediation_candidate = AsyncMock(
        side_effect=RuntimeError("store unavailable")
    )

    result = await workflow_remediation.create_candidate_for_failed_run(
        repository=repository,
        history_store=None,
        run=run,
        exc=RuntimeError("boom"),
    )

    assert result is None


@pytest.mark.asyncio
async def test_load_run_history_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await workflow_remediation._load_run_history(None, uuid4(), uuid4()) is None

    store = AsyncMock()
    store.get_history = AsyncMock(side_effect=RunHistoryNotFoundError("missing"))
    assert await workflow_remediation._load_run_history(store, uuid4(), uuid4()) is None

    store.get_history = AsyncMock(side_effect=RuntimeError("down"))
    assert await workflow_remediation._load_run_history(store, uuid4(), uuid4()) is None

    class History:
        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            del mode
            return {"steps": []}

    store.get_history = AsyncMock(return_value=History())
    assert await workflow_remediation._load_run_history(store, uuid4(), uuid4()) == {
        "steps": []
    }

    store.get_history = AsyncMock(return_value=object())
    assert await workflow_remediation._load_run_history(store, uuid4(), uuid4()) is None


def test_settings_float_falls_back_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_remediation,
        "settings_value",
        lambda *_args, **_kwargs: "not-a-float",
    )

    assert (
        workflow_remediation._settings_float(
            settings=None,
            attr_path="workflow_autofix.idle_load_threshold",
            env_key="ORCHEO_WORKFLOW_AUTOFIX_IDLE_LOAD_THRESHOLD",
            default=1.5,
        )
        == 1.5
    )


def test_guess_failed_component_branches() -> None:
    assert workflow_remediation._guess_failed_component(None) is None
    assert workflow_remediation._guess_failed_component({"steps": []}) is None
    assert (
        workflow_remediation._guess_failed_component(
            {"steps": [{"payload": {"node": "rss"}}]}
        )
        == "rss"
    )
    assert (
        workflow_remediation._guess_failed_component(
            {"steps": [{"payload": {"component": "rss"}}]}
        )
        == "component"
    )
    assert (
        workflow_remediation._guess_failed_component(
            {"steps": [{"payload": {"node": "rss"}}, {"payload": "ignored"}]}
        )
        == "rss"
    )
    assert (
        workflow_remediation._guess_failed_component(
            {"steps": [{"payload": {"first": "one", "second": "two"}}]}
        )
        is None
    )


def test_workflow_source_and_candidate_source_branches() -> None:
    assert workflow_remediation._workflow_source("text") is None
    assert workflow_remediation._workflow_source({"source": "  "}) is None
    assert (
        workflow_remediation._workflow_source({"source": "print('ok')"})
        == "print('ok')"
    )

    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.PENDING)
    candidate.context["workflow_source"] = "  "
    with pytest.raises(ValueError, match="workflow script source"):
        workflow_remediation._candidate_workflow_source(candidate)


def test_celery_workflow_load_and_count_execution_tasks() -> None:
    assert (
        workflow_remediation._count_execution_tasks(
            {
                "worker-1": [
                    {"name": "orcheo_backend.worker.tasks.execute_run"},
                    {"name": "other"},
                    42,
                ],
                "worker-2": "unexpected",
            }
        )
        == 1
    )

    class Inspect:
        def active(self) -> dict[str, Any]:
            return {"worker": [{"name": "orcheo_backend.worker.tasks.execute_run"}]}

        def reserved(self) -> dict[str, Any]:
            return {"worker": [{"name": "noop"}]}

    class CeleryApp:
        class control:
            @staticmethod
            def inspect() -> Any:
                return Inspect()

    assert workflow_remediation._celery_workflow_load(CeleryApp()) == 1

    class FailingCeleryApp:
        class control:
            @staticmethod
            def inspect() -> Any:
                raise RuntimeError("boom")

    assert workflow_remediation._celery_workflow_load(FailingCeleryApp()) == 1


def test_host_load_average_and_cleanup_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_remediation.os,
        "getloadavg",
        lambda: (_ for _ in ()).throw(OSError("unsupported")),
    )
    assert workflow_remediation._host_load_average() is None

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("data", encoding="utf-8")
    workflow_remediation.cleanup_workspace(workspace)
    assert not workspace.exists()


def test_validate_workspace_artifact_boundary_rejects_unexpected_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "artifact"
    workspace.mkdir()
    (workspace / "classification.json").write_text("{}", encoding="utf-8")
    (workspace / "developer_note.md").write_text("note", encoding="utf-8")
    (workspace / "validation_report.json").write_text("{}", encoding="utf-8")
    (workspace / "workflow.py").write_text("print('ok')", encoding="utf-8")
    pycache = workspace / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.pyc").write_bytes(b"pyc")
    extra = workspace / "extra.txt"
    extra.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError, match="Unexpected remediation artifact"):
        workflow_remediation._validate_workspace_artifact_boundary(workspace)


def test_validate_workspace_artifact_boundary_rejects_symlinks(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "artifact"
    workspace.mkdir()
    (workspace / "classification.json").write_text("{}", encoding="utf-8")
    (workspace / "developer_note.md").write_text("note", encoding="utf-8")
    (workspace / "validation_report.json").write_text("{}", encoding="utf-8")
    (workspace / "workflow.py").write_text("print('ok')", encoding="utf-8")
    target = workspace / "workflow.py"
    link = workspace / "linked.py"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="Unexpected remediation artifact"):
        workflow_remediation._validate_workspace_artifact_boundary(workspace)


def test_default_action_mapping_and_float_coercion() -> None:
    assert (
        workflow_remediation._default_action_for_classification(
            WorkflowRunRemediationClassification.WORKFLOW_FIXABLE
        )
        == "create_workflow_version"
    )
    assert (
        workflow_remediation._default_action_for_classification(
            WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM
        )
        == "note_only"
    )
    assert workflow_remediation._mapping_payload({"a": 1}) == {"a": 1}
    assert workflow_remediation._mapping_payload("nope") == {}
    assert workflow_remediation._coerce_optional_float(None) is None
    assert workflow_remediation._coerce_optional_float("1.5") == 1.5
    assert workflow_remediation._coerce_optional_float("bad") is None


def test_parse_remediation_artifacts_reports_validation_errors(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "workflow.py").write_text("print('ok')", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing remediation artifact"):
        workflow_remediation.parse_remediation_artifacts(
            missing,
            original_source="print('ok')",
        )

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    _write_artifacts(
        invalid,
        classification="bogus",
        action="note_only",
        workflow_source="print('ok')",
    )
    with pytest.raises(ValueError, match="Unsupported remediation classification"):
        workflow_remediation.parse_remediation_artifacts(
            invalid,
            original_source="print('ok')",
        )

    empty_note = tmp_path / "empty-note"
    empty_note.mkdir()
    _write_artifacts(
        empty_note,
        classification="runtime_or_platform",
        action="note_only",
        workflow_source="print('ok')",
        developer_note="",
    )
    with pytest.raises(ValueError, match="developer_note.md must not be empty"):
        workflow_remediation.parse_remediation_artifacts(
            empty_note,
            original_source="print('ok')",
        )

    invalid_action = tmp_path / "invalid-action"
    invalid_action.mkdir()
    _write_artifacts(
        invalid_action,
        classification="runtime_or_platform",
        action="bogus",
        workflow_source="print('ok')",
    )
    with pytest.raises(ValueError, match="Unsupported remediation action"):
        workflow_remediation.parse_remediation_artifacts(
            invalid_action,
            original_source="print('ok')",
        )

    note_only_fix = tmp_path / "note-only-fix"
    note_only_fix.mkdir()
    _write_artifacts(
        note_only_fix,
        classification="workflow_fixable",
        action="note_only",
        workflow_source="print('ok')",
    )
    with pytest.raises(
        ValueError,
        match="Workflow-fix classifications cannot request note-only action",
    ):
        workflow_remediation.parse_remediation_artifacts(
            note_only_fix,
            original_source="print('ok')",
        )

    fixed_only = tmp_path / "fixed-only"
    fixed_only.mkdir()
    _write_artifacts(
        fixed_only,
        classification="workflow_fixable",
        action="note_only",
        workflow_source="print('ok')",
    )
    with pytest.raises(ValueError, match="Workflow-fix classifications"):
        workflow_remediation.parse_remediation_artifacts(
            fixed_only,
            original_source="print('ok')",
        )


class _IdleRepo:
    def __init__(
        self,
        *,
        claimed: int = 0,
        running: int = 0,
    ) -> None:
        self.claimed = claimed
        self.running = running

    async def list_remediation_candidates(
        self,
        *,
        status: WorkflowRunRemediationStatus,
        limit: int,
    ) -> list[object]:
        del status, limit
        return [object()] * self.claimed

    async def list_workflows(self, include_archived: bool) -> list[SimpleNamespace]:
        del include_archived
        return [SimpleNamespace(id=uuid4())]

    async def list_runs_for_workflow(
        self,
        workflow_id: Any,
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        del workflow_id, limit
        return [SimpleNamespace(status=WorkflowRunStatus.RUNNING)] * self.running


@pytest.mark.asyncio
async def test_evaluate_remediation_idle_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Inspect:
        def active(self) -> dict[str, Any]:
            return {"worker": [{"name": "orcheo_backend.worker.tasks.execute_run"}]}

        def reserved(self) -> dict[str, Any]:
            return {}

    class CeleryApp:
        class control:
            @staticmethod
            def inspect() -> Any:
                return Inspect()

    monkeypatch.setattr(workflow_remediation.os, "getloadavg", lambda: (0.1, 0.0, 0.0))

    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(claimed=2),
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            max_concurrent_attempts=1,
        ),
    )
    assert decision.reason == "remediation_concurrency_limit"

    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(claimed=0, running=1),
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert decision.reason == "active_workflow_runs"

    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(),
        celery_app=CeleryApp(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert decision.reason == "celery_workflow_load"

    monkeypatch.setattr(
        workflow_remediation.os,
        "getloadavg",
        lambda: (_ for _ in ()).throw(OSError("unsupported")),
    )
    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(),
        celery_app=SimpleNamespace(
            control=SimpleNamespace(
                inspect=lambda: SimpleNamespace(
                    active=lambda: {},
                    reserved=lambda: {},
                )
            )
        ),
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            unknown_load_allows_remediation=True,
        ),
    )
    assert decision.is_idle is True
    assert decision.reason == "host_load_unknown"

    monkeypatch.setattr(workflow_remediation.os, "getloadavg", lambda: (3.0, 0.0, 0.0))
    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(),
        celery_app=SimpleNamespace(
            control=SimpleNamespace(
                inspect=lambda: SimpleNamespace(
                    active=lambda: {},
                    reserved=lambda: {},
                )
            )
        ),
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            idle_load_threshold=1.0,
        ),
    )
    assert decision.reason == "host_load_high"

    monkeypatch.setattr(workflow_remediation.os, "getloadavg", lambda: (0.1, 0.0, 0.0))
    decision = await workflow_remediation.evaluate_remediation_idle(
        repository=_IdleRepo(),
        celery_app=SimpleNamespace(
            control=SimpleNamespace(
                inspect=lambda: SimpleNamespace(
                    active=lambda: {},
                    reserved=lambda: {},
                )
            )
        ),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert decision.is_idle is True
    assert decision.reason == "idle"


@pytest.mark.asyncio
async def test_scan_workflow_remediations_async_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.PENDING)
    repository = AsyncMock()
    repository.claim_next_remediation_candidate = AsyncMock(return_value=candidate)
    repository.mark_remediation_failed = AsyncMock()

    monkeypatch.setattr(
        workflow_remediation,
        "evaluate_remediation_idle",
        AsyncMock(
            return_value=workflow_remediation.IdleDecision(
                False, "disabled", {"detail": "nope"}
            )
        ),
    )
    skipped = await workflow_remediation.scan_workflow_remediations_async(
        repository=repository,
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert skipped["status"] == "skipped"

    monkeypatch.setattr(
        workflow_remediation,
        "evaluate_remediation_idle",
        AsyncMock(return_value=workflow_remediation.IdleDecision(True, "idle", {})),
    )
    repository.claim_next_remediation_candidate = AsyncMock(return_value=None)
    idle = await workflow_remediation.scan_workflow_remediations_async(
        repository=repository,
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert idle["status"] == "idle"

    repository.claim_next_remediation_candidate = AsyncMock(return_value=candidate)
    task = MagicMock()
    task.delay.side_effect = RuntimeError("queue down")
    monkeypatch.setattr(
        "orcheo_backend.worker.tasks.attempt_workflow_remediation", task
    )
    failed = await workflow_remediation.scan_workflow_remediations_async(
        repository=repository,
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert failed["status"] == "failed_to_enqueue"
    repository.mark_remediation_failed.assert_awaited()

    task.delay.side_effect = None
    claimed = await workflow_remediation.scan_workflow_remediations_async(
        repository=repository,
        celery_app=object(),
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )
    assert claimed["status"] == "claimed"


@pytest.mark.asyncio
async def test_attempt_workflow_remediation_async_skips_unclaimed_candidate() -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.PENDING)
    repository = AsyncMock()
    repository.get_remediation_candidate = AsyncMock(return_value=candidate)

    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(enabled=True),
    )

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_attempt_workflow_remediation_async_dry_run_and_note_only(
    tmp_path: Path,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    candidate.context["workflow_source"] = WORKFLOW_SCRIPT
    repository = AsyncMock()
    repository.get_remediation_candidate = AsyncMock(return_value=candidate)
    repository.mark_remediation_note_only = AsyncMock()
    version = WorkflowVersion.model_construct(
        id=candidate.workflow_version_id,
        workflow_id=candidate.workflow_id,
        version=1,
        graph={
            "format": "langgraph_script",
            "source": WORKFLOW_SCRIPT,
            "entrypoint": "build_graph",
        },
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="tester",
    )
    repository.get_version = AsyncMock(return_value=version)

    async def fake_agent(
        workspace: Path,
        _candidate: WorkflowRunRemediation,
        _settings: workflow_remediation.WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        _write_artifacts(
            workspace,
            classification="workflow_fixable",
            action="create_workflow_version",
            workflow_source=WORKFLOW_SCRIPT + "\n# fixed\n",
        )
        return {"provider": "fake"}

    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            dry_run=True,
        ),
        agent_invoker=fake_agent,
    )

    assert result["status"] == "dry_run"
    repository.mark_remediation_note_only.assert_awaited_once()


@pytest.mark.asyncio
async def test_attempt_workflow_remediation_async_rejects_unchanged_source(
    tmp_path: Path,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    source = "print('same')"
    candidate.context["workflow_source"] = source
    repository = AsyncMock()
    repository.get_remediation_candidate = AsyncMock(return_value=candidate)
    repository.mark_remediation_failed = AsyncMock()
    version = WorkflowVersion.model_construct(
        id=candidate.workflow_version_id,
        workflow_id=candidate.workflow_id,
        version=1,
        graph={"format": "langgraph_script", "source": source},
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="tester",
    )
    repository.get_version = AsyncMock(return_value=version)

    async def fake_agent(
        workspace: Path,
        _candidate: WorkflowRunRemediation,
        _settings: workflow_remediation.WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        _write_artifacts(
            workspace,
            classification="workflow_fixable",
            action="create_workflow_version",
            workflow_source=source,
        )
        return {"provider": "fake"}

    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            dry_run=False,
        ),
        agent_invoker=fake_agent,
    )

    assert result["status"] == "failed"
    assert "did not change workflow.py" in result["error"]
    repository.mark_remediation_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_attempt_workflow_remediation_async_handles_ingest_and_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    candidate.context["workflow_source"] = WORKFLOW_SCRIPT
    repository = AsyncMock()
    repository.get_remediation_candidate = AsyncMock(return_value=candidate)
    repository.mark_remediation_failed = AsyncMock()
    repository.mark_remediation_fixed = AsyncMock()
    version = WorkflowVersion.model_construct(
        id=candidate.workflow_version_id,
        workflow_id=candidate.workflow_id,
        version=1,
        graph={
            "format": "langgraph_script",
            "source": WORKFLOW_SCRIPT,
            "entrypoint": "build_graph",
        },
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="tester",
    )
    repository.get_version = AsyncMock(return_value=version)
    created_version = WorkflowVersion.model_construct(
        id=uuid4(),
        workflow_id=candidate.workflow_id,
        version=2,
        graph={
            "format": "langgraph_script",
            "source": WORKFLOW_SCRIPT + "\n# fixed\n",
            "entrypoint": "build_graph",
        },
        metadata={},
        runnable_config=None,
        notes=None,
        created_by="orcheo-vibe-remediation",
    )
    repository.create_version = AsyncMock(return_value=created_version)

    async def fake_agent(
        workspace: Path,
        _candidate: WorkflowRunRemediation,
        _settings: workflow_remediation.WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        _write_artifacts(
            workspace,
            classification="workflow_fixable",
            action="create_workflow_version",
            workflow_source=WORKFLOW_SCRIPT + "\n# fixed\n",
        )
        return {"provider": "fake"}

    monkeypatch.setattr(
        workflow_remediation,
        "_retry_fixed_workflow_run",
        AsyncMock(return_value={"enabled": True, "created": True}),
    )
    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            dry_run=False,
            retry_after_fix=True,
        ),
        agent_invoker=fake_agent,
    )

    assert result["status"] == "fixed"
    repository.mark_remediation_fixed.assert_awaited_once()
    assert result["retry_after_fix"] == {"enabled": True, "created": True}

    monkeypatch.setattr(
        workflow_remediation,
        "ingest_langgraph_script",
        MagicMock(side_effect=workflow_remediation.ScriptIngestionError("bad script")),
    )
    repository.mark_remediation_failed.reset_mock()
    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            dry_run=False,
        ),
        agent_invoker=fake_agent,
    )

    assert result["status"] == "failed"
    assert "bad script" in result["error"]
    repository.mark_remediation_failed.assert_awaited()


@pytest.mark.asyncio
async def test_attempt_workflow_remediation_async_catches_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    candidate.context["workflow_source"] = WORKFLOW_SCRIPT
    repository = AsyncMock()
    repository.get_remediation_candidate = AsyncMock(return_value=candidate)
    repository.get_version = AsyncMock(side_effect=RuntimeError("version missing"))
    repository.mark_remediation_failed = AsyncMock()

    async def fake_agent(
        workspace: Path,
        _candidate: WorkflowRunRemediation,
        _settings: workflow_remediation.WorkflowAutofixSettings,
    ) -> dict[str, Any]:
        _write_artifacts(
            workspace,
            classification="workflow_fixable",
            action="create_workflow_version",
            workflow_source=WORKFLOW_SCRIPT + "\n# fixed\n",
        )
        return {"provider": "fake"}

    result = await workflow_remediation.attempt_workflow_remediation_async(
        repository=repository,
        remediation_id=candidate.id,
        settings=workflow_remediation.WorkflowAutofixSettings(
            enabled=True,
            dry_run=False,
        ),
        agent_invoker=fake_agent,
    )

    assert result["status"] == "failed"
    assert "version missing" in result["error"]
    repository.mark_remediation_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_orcheo_vibe_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow_source = "print('before')"
    workflow_remediation._materialize_workspace(workspace, candidate, workflow_source)

    class Runtime:
        version = "1.0"

    class Resolution:
        runtime = Runtime()

    class Provider:
        def __init__(self, *, authenticated: bool) -> None:
            self.authenticated = authenticated

        def probe_auth(self, runtime: Any, environ: dict[str, str]) -> Any:
            del runtime, environ
            return SimpleNamespace(
                authenticated=self.authenticated,
                message="not authenticated" if not self.authenticated else None,
            )

        def build_command(
            self, runtime: Any, *, prompt: str, system_prompt: str
        ) -> list[str]:
            del runtime, prompt, system_prompt
            return ["orcheo", "vibe"]

        def build_environment(self, environment: dict[str, str]) -> dict[str, str]:
            return dict(environment)

        def execution_audit_metadata(
            self,
            runtime: Any,
            *,
            command: list[str],
            working_directory: Path,
        ) -> dict[str, Any]:
            del runtime, command, working_directory
            return {"audit": True}

    class Manager:
        def __init__(self, *, authenticated: bool) -> None:
            self._provider = Provider(authenticated=authenticated)

        async def resolve_runtime(self, provider_name: str) -> Any:
            del provider_name
            return Resolution()

        def get_provider(self, provider_name: str) -> Provider:
            del provider_name
            return self._provider

        def environment_for_provider(self, provider_name: str) -> dict[str, str]:
            del provider_name
            return {"TOKEN": "1"}

    success_process = AsyncMock(
        return_value=SimpleNamespace(
            exit_code=0,
            timed_out=False,
            duration_seconds=1.5,
            stdout="out",
            stderr="err",
        )
    )
    monkeypatch.setattr(
        workflow_remediation,
        "ExternalAgentRuntimeManager",
        lambda: Manager(authenticated=True),
    )
    monkeypatch.setattr(workflow_remediation, "execute_process", success_process)
    metadata = await workflow_remediation._invoke_orcheo_vibe(
        workspace,
        candidate,
        workflow_remediation.WorkflowAutofixSettings(agent_provider="codex"),
    )
    assert metadata["provider"] == "codex"
    assert metadata["execution_audit"] == {"audit": True}

    class ProviderWithoutAudit(Provider):
        def execution_audit_metadata(
            self,
            runtime: Any,
            *,
            command: list[str],
            working_directory: Path,
        ) -> None:
            del runtime, command, working_directory

    class ManagerWithoutAudit(Manager):
        def __init__(self) -> None:
            self._provider = ProviderWithoutAudit(authenticated=True)

    monkeypatch.setattr(
        workflow_remediation,
        "ExternalAgentRuntimeManager",
        lambda: ManagerWithoutAudit(),
    )
    monkeypatch.setattr(workflow_remediation, "execute_process", success_process)
    metadata_without_audit = await workflow_remediation._invoke_orcheo_vibe(
        workspace,
        candidate,
        workflow_remediation.WorkflowAutofixSettings(agent_provider="codex"),
    )
    assert "execution_audit" not in metadata_without_audit

    monkeypatch.setattr(
        workflow_remediation,
        "ExternalAgentRuntimeManager",
        lambda: Manager(authenticated=False),
    )
    with pytest.raises(RuntimeError, match="not authenticated"):
        await workflow_remediation._invoke_orcheo_vibe(
            workspace,
            candidate,
            workflow_remediation.WorkflowAutofixSettings(agent_provider="codex"),
        )

    failing_process = AsyncMock(
        return_value=SimpleNamespace(
            exit_code=1,
            timed_out=False,
            duration_seconds=1.5,
            stdout="out",
            stderr="err",
        )
    )
    monkeypatch.setattr(
        workflow_remediation,
        "ExternalAgentRuntimeManager",
        lambda: Manager(authenticated=True),
    )
    monkeypatch.setattr(workflow_remediation, "execute_process", failing_process)
    with pytest.raises(RuntimeError, match="exit code 1"):
        await workflow_remediation._invoke_orcheo_vibe(
            workspace,
            candidate,
            workflow_remediation.WorkflowAutofixSettings(agent_provider="codex"),
        )


@pytest.mark.asyncio
async def test_retry_fixed_workflow_run_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _remediation_candidate(status=WorkflowRunRemediationStatus.CLAIMED)
    source_run = SimpleNamespace(
        input_payload={"a": 1},
        runnable_config={"tags": ["run"]},
        id=candidate.run_id,
    )
    repository = AsyncMock()
    repository.get_run = AsyncMock(return_value=source_run)
    repository.create_run = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

    execute_run_mock = MagicMock()
    execute_run_mock.delay = MagicMock()
    monkeypatch.setattr("orcheo_backend.worker.tasks.execute_run", execute_run_mock)

    created = await workflow_remediation._retry_fixed_workflow_run(
        repository=repository,
        candidate=candidate,
        created_version_id=uuid4(),
    )
    assert created["created"] is True
    assert created["enqueued"] is True

    repository.get_run = AsyncMock(return_value=source_run)
    repository.create_run = AsyncMock(side_effect=RuntimeError("create failed"))
    created_failure = await workflow_remediation._retry_fixed_workflow_run(
        repository=repository,
        candidate=candidate,
        created_version_id=uuid4(),
    )
    assert created_failure["created"] is False
    assert created_failure["error"] == "create failed"

    repository.get_run = AsyncMock(side_effect=RuntimeError("missing"))
    failed = await workflow_remediation._retry_fixed_workflow_run(
        repository=repository,
        candidate=candidate,
        created_version_id=uuid4(),
    )
    assert failed["created"] is False

    repository.get_run = AsyncMock(return_value=source_run)
    repository.create_run = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    execute_run_mock.delay.side_effect = RuntimeError("queue down")
    queued = await workflow_remediation._retry_fixed_workflow_run(
        repository=repository,
        candidate=candidate,
        created_version_id=uuid4(),
    )
    assert queued["enqueued"] is False
