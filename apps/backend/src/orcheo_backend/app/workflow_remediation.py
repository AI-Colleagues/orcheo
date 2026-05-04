"""Workflow autofix capture, supervision, and artifact processing."""

from __future__ import annotations
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import tempfile
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID
from orcheo.external_agents import ExternalAgentRuntimeManager, execute_process
from orcheo.graph.ingestion import ScriptIngestionError, ingest_langgraph_script
from orcheo.models.workflow import (
    WorkflowRun,
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
    WorkflowRunStatus,
    WorkflowVersion,
)
from orcheo_backend.app.history import RunHistoryNotFoundError
from orcheo_backend.app.providers import settings_value
from orcheo_backend.app.repository import WorkflowRepository


logger = logging.getLogger(__name__)

REDACTED = "[[REDACTED]]"
AUTOFIX_ACTOR = "orcheo-vibe-remediation"
CLASSIFICATION_VALUES = {item.value for item in WorkflowRunRemediationClassification}
ACTION_VALUES = {"create_workflow_version", "note_only"}
NOTE_ONLY_CLASSIFICATIONS = {
    WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
    WorkflowRunRemediationClassification.EXTERNAL_DEPENDENCY,
    WorkflowRunRemediationClassification.UNKNOWN,
}
ALLOWED_WORKSPACE_FILES = {
    "classification.json",
    "developer_note.md",
    "failure.json",
    "instructions.md",
    "per_run_runnable_config.json",
    "run_history.json",
    "stored_runnable_config.json",
    "validation_report.json",
    "workflow.py",
}
SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|auth|authorization|bearer|cookie|credential|password|secret|token)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
TOKEN_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})\b"
)
LONG_TOKEN_PATTERN = re.compile(
    r"\b(?=[A-Za-z0-9._~+/=-]{32,}\b)(?=.*[A-Za-z])(?=.*\d)"
    r"[A-Za-z0-9._~+/=-]+\b"
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|auth|authorization|bearer|cookie|credential|password|secret|token)"
    r"\s*[:=]\s*([\"']?)[^\"'\s,;]+(\2)"
)
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class WorkflowAutofixSettings:
    """Settings that gate automatic workflow remediation."""

    enabled: bool = False
    max_concurrent_attempts: int = 1
    idle_load_threshold: float = 1.5
    dry_run: bool = True
    max_attempts_per_candidate: int = 3
    unknown_load_allows_remediation: bool = False
    agent_provider: str = "codex"
    agent_timeout_seconds: int = 900
    retry_after_fix: bool = False


@dataclass(frozen=True, slots=True)
class IdleDecision:
    """Result of worker idle-gate evaluation."""

    is_idle: bool
    reason: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemediationArtifacts:
    """Parsed Orcheo Vibe remediation artifacts."""

    classification: WorkflowRunRemediationClassification
    confidence: float | None
    action: str
    summary: str
    requires_human_review: bool
    suspected_component: dict[str, Any]
    developer_note: str
    validation_report: dict[str, Any]
    workflow_source: str
    original_source_hash: str
    workflow_source_hash: str
    artifact_hashes: dict[str, str]
    provider_metadata: dict[str, Any]

    @property
    def source_changed(self) -> bool:
        """Return whether the workflow source differs from the failed version."""
        return self.workflow_source_hash != self.original_source_hash


AgentInvoker = Callable[
    [Path, WorkflowRunRemediation, WorkflowAutofixSettings],
    Awaitable[dict[str, Any]],
]


def load_workflow_autofix_settings(
    settings: object | None = None,
) -> WorkflowAutofixSettings:
    """Read workflow autofix settings from Dynaconf-like settings and env."""
    return WorkflowAutofixSettings(
        enabled=_settings_bool(
            settings,
            "workflow_autofix.enabled",
            "ORCHEO_WORKFLOW_AUTOFIX_ENABLED",
            False,
        ),
        max_concurrent_attempts=max(
            1,
            _settings_int(
                settings,
                "workflow_autofix.max_concurrent_attempts",
                "ORCHEO_WORKFLOW_AUTOFIX_MAX_CONCURRENT_ATTEMPTS",
                1,
            ),
        ),
        idle_load_threshold=max(
            0.0,
            _settings_float(
                settings,
                "workflow_autofix.idle_load_threshold",
                "ORCHEO_WORKFLOW_AUTOFIX_IDLE_LOAD_THRESHOLD",
                1.5,
            ),
        ),
        dry_run=_settings_bool(
            settings,
            "workflow_autofix.dry_run",
            "ORCHEO_WORKFLOW_AUTOFIX_DRY_RUN",
            True,
        ),
        max_attempts_per_candidate=max(
            1,
            _settings_int(
                settings,
                "workflow_autofix.max_attempts_per_candidate",
                "ORCHEO_WORKFLOW_AUTOFIX_MAX_ATTEMPTS_PER_CANDIDATE",
                3,
            ),
        ),
        unknown_load_allows_remediation=_settings_bool(
            settings,
            "workflow_autofix.unknown_load_allows_remediation",
            "ORCHEO_WORKFLOW_AUTOFIX_UNKNOWN_LOAD_ALLOWS_REMEDIATION",
            False,
        ),
        agent_provider=str(
            settings_value(
                settings,
                attr_path="workflow_autofix.agent_provider",
                env_key="ORCHEO_WORKFLOW_AUTOFIX_AGENT_PROVIDER",
                default=os.getenv("ORCHEO_WORKFLOW_AUTOFIX_AGENT_PROVIDER", "codex"),
            )
        ),
        agent_timeout_seconds=max(
            1,
            _settings_int(
                settings,
                "workflow_autofix.agent_timeout_seconds",
                "ORCHEO_WORKFLOW_AUTOFIX_AGENT_TIMEOUT_SECONDS",
                900,
            ),
        ),
        retry_after_fix=_settings_bool(
            settings,
            "workflow_autofix.retry_after_fix",
            "ORCHEO_WORKFLOW_AUTOFIX_RETRY_AFTER_FIX",
            False,
        ),
    )


def redact_sensitive_values(value: Any) -> Any:  # noqa: C901, PLR0911
    """Return a redacted JSON-compatible copy of an arbitrary value."""
    if inspect.isawaitable(value):
        return _redact_text(str(value))
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_PATTERN.search(key_text):
                redacted[key_text] = (
                    item
                    if isinstance(item, str) and _is_vault_placeholder(item)
                    else REDACTED
                )
            else:
                redacted[key_text] = redact_sensitive_values(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_values(item) for item in value]
    if isinstance(value, tuple | set | frozenset):
        return [redact_sensitive_values(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if inspect.isawaitable(dumped):
            return _redact_text(str(value))
        return redact_sensitive_values(dumped)
    return _redact_text(str(value))


def compute_error_fingerprint(
    *,
    version_checksum: str,
    exception_type: str,
    message: str,
    phase: str | None = None,
    failed_component: str | None = None,
) -> str:
    """Compute a stable error fingerprint for candidate deduplication."""
    payload = {
        "version_checksum": version_checksum,
        "exception_type": exception_type,
        "message": _normalize_error_message(message),
        "phase": phase or "execution",
        "failed_component": failed_component or "",
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def create_candidate_for_failed_run(
    *,
    repository: WorkflowRepository,
    history_store: Any | None,
    run: WorkflowRun,
    exc: BaseException,
    phase: str = "execution",
) -> WorkflowRunRemediation | None:
    """Best-effort capture of a failed run as a remediation candidate."""
    try:
        version = await repository.get_version(run.workflow_version_id)
        current_run = await repository.get_run(run.id)
        if not isinstance(version, WorkflowVersion) or not isinstance(
            current_run,
            WorkflowRun,
        ):
            return None
        run_history = await _load_run_history(
            history_store,
            version.workflow_id,
            run.id,
        )
        formatted_traceback = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        failed_component = _guess_failed_component(run_history)
        exception_type = type(exc).__name__
        error_message = str(exc)
        graph_format = None
        workflow_entrypoint = None
        if isinstance(version.graph, dict):
            raw_format = version.graph.get("format")
            graph_format = str(raw_format) if raw_format is not None else None
            raw_entrypoint = version.graph.get("entrypoint")
            workflow_entrypoint = (
                str(raw_entrypoint) if raw_entrypoint is not None else None
            )
        version_checksum = version.compute_checksum()
        normalized_error_message = _normalize_error_message(error_message)
        context = redact_sensitive_values(
            {
                "workflow_id": str(version.workflow_id),
                "workflow_version_id": str(version.id),
                "run_id": str(run.id),
                "version_checksum": version_checksum,
                "graph_format": graph_format,
                "phase": phase,
                "exception_type": exception_type,
                "error_message": error_message,
                "normalized_error_message": normalized_error_message,
                "traceback": formatted_traceback,
                "inputs": current_run.input_payload,
                "input_payload": current_run.input_payload,
                "per_run_runnable_config": current_run.runnable_config,
                "run_runnable_config": current_run.runnable_config,
                "stored_version_runnable_config": version.runnable_config,
                "version_runnable_config": version.runnable_config,
                "recent_run_history": run_history,
                "failed_component": failed_component,
                "workflow_source": _workflow_source(version.graph),
                "workflow_entrypoint": workflow_entrypoint,
            }
        )
        fingerprint = compute_error_fingerprint(
            version_checksum=version_checksum,
            exception_type=exception_type,
            message=error_message,
            phase=phase,
            failed_component=failed_component,
        )
        candidate = await repository.create_remediation_candidate(
            workflow_id=version.workflow_id,
            workflow_version_id=version.id,
            run_id=run.id,
            fingerprint=fingerprint,
            version_checksum=version_checksum,
            graph_format=graph_format,
            context=context,
        )
        logger.info(
            "Workflow remediation candidate recorded "
            "remediation_id=%s workflow_id=%s workflow_version_id=%s run_id=%s "
            "status=%s fingerprint=%s",
            candidate.id,
            version.workflow_id,
            version.id,
            run.id,
            candidate.status.value,
            fingerprint,
        )
        return candidate
    except Exception:
        logger.exception(
            "Failed to create workflow remediation candidate for run %s",
            run.id,
        )
        return None


async def evaluate_remediation_idle(  # noqa: PLR0911
    *,
    repository: WorkflowRepository,
    celery_app: Any,
    settings: WorkflowAutofixSettings,
) -> IdleDecision:
    """Return whether the worker appears idle enough to run remediation."""
    if not settings.enabled:
        return IdleDecision(False, "disabled", {})

    claimed = await repository.list_remediation_candidates(
        status=WorkflowRunRemediationStatus.CLAIMED,
        limit=settings.max_concurrent_attempts,
    )
    if len(claimed) >= settings.max_concurrent_attempts:
        return IdleDecision(
            False,
            "remediation_concurrency_limit",
            {"claimed_remediations": len(claimed)},
        )

    active_runs = await _count_active_workflow_runs(repository)
    if active_runs > 0:
        return IdleDecision(
            False,
            "active_workflow_runs",
            {"active_runs": active_runs},
        )

    celery_load = _celery_workflow_load(celery_app)
    if celery_load > 0:
        return IdleDecision(
            False,
            "celery_workflow_load",
            {"celery_load": celery_load},
        )

    host_load = _host_load_average()
    if host_load is None:
        return IdleDecision(
            settings.unknown_load_allows_remediation,
            "host_load_unknown",
            {"host_load": None},
        )
    if host_load > settings.idle_load_threshold:
        return IdleDecision(
            False,
            "host_load_high",
            {"host_load": host_load, "threshold": settings.idle_load_threshold},
        )
    return IdleDecision(True, "idle", {"host_load": host_load})


async def scan_workflow_remediations_async(
    *,
    repository: WorkflowRepository,
    celery_app: Any,
    settings: WorkflowAutofixSettings,
) -> dict[str, Any]:
    """Claim one pending remediation when idle and enqueue its attempt task."""
    decision = await evaluate_remediation_idle(
        repository=repository,
        celery_app=celery_app,
        settings=settings,
    )
    if not decision.is_idle:
        logger.info(
            "Workflow remediation scan skipped reason=%s details=%s",
            decision.reason,
            decision.details,
        )
        return {"status": "skipped", "reason": decision.reason, **decision.details}

    candidate = await repository.claim_next_remediation_candidate(
        actor=AUTOFIX_ACTOR,
        max_attempts=settings.max_attempts_per_candidate,
    )
    if candidate is None:
        logger.info("Workflow remediation scan found no pending candidates")
        return {"status": "idle", "claimed": None}

    from orcheo_backend.worker.tasks import attempt_workflow_remediation

    attempt_workflow_remediation.delay(str(candidate.id))
    logger.info(
        "Workflow remediation candidate claimed remediation_id=%s attempt_count=%s",
        candidate.id,
        candidate.attempt_count,
    )
    return {"status": "claimed", "remediation_id": str(candidate.id)}


async def attempt_workflow_remediation_async(  # noqa: PLR0911
    *,
    repository: WorkflowRepository,
    remediation_id: UUID,
    settings: WorkflowAutofixSettings,
    agent_invoker: AgentInvoker | None = None,
) -> dict[str, Any]:
    """Run one claimed workflow remediation and persist its outcome."""
    candidate = await repository.get_remediation_candidate(remediation_id)
    try:
        if candidate.status is not WorkflowRunRemediationStatus.CLAIMED:
            logger.info(
                "Workflow remediation attempt skipped remediation_id=%s status=%s",
                remediation_id,
                candidate.status.value,
            )
            return {"status": "skipped", "reason": f"status={candidate.status.value}"}
        source = _candidate_workflow_source(candidate)
        with tempfile.TemporaryDirectory(prefix="orcheo-remediation-") as workspace_raw:
            workspace = Path(workspace_raw)
            prompt_hash = _materialize_workspace(workspace, candidate, source)
            provider_metadata = await (agent_invoker or _invoke_orcheo_vibe)(
                workspace,
                candidate,
                settings,
            )
            artifacts = parse_remediation_artifacts(
                workspace,
                original_source=source,
                provider_metadata=provider_metadata,
            )
            artifacts_payload = _artifact_payload(
                artifacts,
                prompt_hash=prompt_hash,
                dry_run=settings.dry_run,
            )
            if artifacts.classification in NOTE_ONLY_CLASSIFICATIONS:
                await repository.mark_remediation_note_only(
                    remediation_id,
                    classification=artifacts.classification,
                    developer_note=artifacts.developer_note,
                    artifacts={
                        **artifacts_payload,
                        "source_change_ignored": artifacts.source_changed,
                    },
                )
                logger.info(
                    "Workflow remediation note-only result remediation_id=%s "
                    "classification=%s source_change_ignored=%s",
                    remediation_id,
                    artifacts.classification.value,
                    artifacts.source_changed,
                )
                return {
                    "status": "note_only",
                    "classification": artifacts.classification.value,
                }

            if settings.dry_run:
                await repository.mark_remediation_note_only(
                    remediation_id,
                    classification=artifacts.classification,
                    developer_note=artifacts.developer_note,
                    artifacts={**artifacts_payload, "dry_run_version_creation": True},
                )
                logger.info(
                    "Workflow remediation dry-run result remediation_id=%s "
                    "classification=%s",
                    remediation_id,
                    artifacts.classification.value,
                )
                return {
                    "status": "dry_run",
                    "classification": artifacts.classification.value,
                }

            if not artifacts.source_changed:
                error = (
                    "Workflow remediation classified as fixable but did not change "
                    "workflow.py."
                )
                await repository.mark_remediation_failed(
                    remediation_id,
                    error=error,
                    artifacts=artifacts_payload,
                    validation_result={"ok": False, "error": error},
                )
                logger.info(
                    "Workflow remediation rejected unchanged source "
                    "remediation_id=%s classification=%s",
                    remediation_id,
                    artifacts.classification.value,
                )
                return {"status": "failed", "error": error}

            version = await repository.get_version(candidate.workflow_version_id)
            entrypoint = (
                version.graph.get("entrypoint")
                if isinstance(version.graph, dict)
                else None
            )
            try:
                graph_payload = ingest_langgraph_script(
                    artifacts.workflow_source,
                    entrypoint=entrypoint,
                )
            except ScriptIngestionError as exc:
                await repository.mark_remediation_failed(
                    remediation_id,
                    error=str(exc),
                    artifacts=artifacts_payload,
                    validation_result={"ok": False, "error": str(exc)},
                )
                logger.info(
                    "Workflow remediation validation failed remediation_id=%s error=%s",
                    remediation_id,
                    str(exc),
                )
                return {"status": "failed", "error": str(exc)}

            validation_result = {
                "ok": True,
                "graph_format": graph_payload.get("format"),
            }
            created_version = await repository.create_version(
                version.workflow_id,
                graph=graph_payload,
                metadata={
                    **version.metadata,
                    "remediation": {
                        "id": str(remediation_id),
                        "source_run_id": str(candidate.run_id),
                        "classification": artifacts.classification.value,
                        "agent_provider": settings.agent_provider,
                        "summary": artifacts.summary,
                    },
                },
                runnable_config=version.runnable_config,
                notes=format_remediation_version_notes(
                    source_run_id=candidate.run_id,
                    remediation_id=remediation_id,
                    classification=artifacts.classification,
                    agent_provider=settings.agent_provider,
                    summary=artifacts.summary,
                    developer_note=artifacts.developer_note,
                ),
                created_by=AUTOFIX_ACTOR,
            )
            retry_metadata: dict[str, Any] = {"enabled": settings.retry_after_fix}
            if settings.retry_after_fix:
                retry_metadata = await _retry_fixed_workflow_run(
                    repository=repository,
                    candidate=candidate,
                    created_version_id=created_version.id,
                )
                artifacts_payload = {
                    **artifacts_payload,
                    "retry_after_fix": retry_metadata,
                }
            await repository.mark_remediation_fixed(
                remediation_id,
                created_version_id=created_version.id,
                classification=artifacts.classification,
                developer_note=artifacts.developer_note,
                artifacts=artifacts_payload,
                validation_result=validation_result,
            )
            logger.info(
                "Workflow remediation fixed workflow remediation_id=%s "
                "workflow_id=%s workflow_version_id=%s created_version_id=%s "
                "classification=%s",
                remediation_id,
                version.workflow_id,
                version.id,
                created_version.id,
                artifacts.classification.value,
            )
            return {
                "status": "fixed",
                "classification": artifacts.classification.value,
                "created_version_id": str(created_version.id),
                "retry_after_fix": retry_metadata,
            }
    except Exception as exc:
        await repository.mark_remediation_failed(remediation_id, error=str(exc))
        logger.exception(
            "Workflow remediation attempt failed remediation_id=%s",
            remediation_id,
        )
        return {"status": "failed", "error": str(exc)}


def parse_remediation_artifacts(
    workspace: Path,
    *,
    original_source: str,
    provider_metadata: dict[str, Any] | None = None,
) -> RemediationArtifacts:
    """Parse and validate the expected Orcheo Vibe output files."""
    classification_path = workspace / "classification.json"
    note_path = workspace / "developer_note.md"
    report_path = workspace / "validation_report.json"
    workflow_path = workspace / "workflow.py"
    missing = [
        path.name
        for path in (classification_path, note_path, report_path, workflow_path)
        if not path.exists()
    ]
    if missing:
        msg = f"Missing remediation artifact(s): {', '.join(missing)}"
        raise ValueError(msg)
    _validate_workspace_artifact_boundary(workspace)

    classification_payload = json.loads(classification_path.read_text(encoding="utf-8"))
    raw_classification = str(classification_payload.get("classification", "")).strip()
    if raw_classification not in CLASSIFICATION_VALUES:
        msg = f"Unsupported remediation classification: {raw_classification!r}"
        raise ValueError(msg)
    classification = WorkflowRunRemediationClassification(raw_classification)
    workflow_source = workflow_path.read_text(encoding="utf-8")
    developer_note = note_path.read_text(encoding="utf-8").strip()
    if not developer_note:
        raise ValueError("developer_note.md must not be empty")
    validation_report = json.loads(report_path.read_text(encoding="utf-8"))
    action = str(classification_payload.get("action", "")).strip()
    if action and action not in ACTION_VALUES:
        msg = f"Unsupported remediation action: {action!r}"
        raise ValueError(msg)
    if (
        classification in NOTE_ONLY_CLASSIFICATIONS
        and action == "create_workflow_version"
    ):
        msg = "Note-only classifications cannot request workflow version creation."
        raise ValueError(msg)
    if classification.creates_workflow_version and action == "note_only":
        msg = "Workflow-fix classifications cannot request note-only action."
        raise ValueError(msg)

    return RemediationArtifacts(
        classification=classification,
        confidence=_coerce_optional_float(classification_payload.get("confidence")),
        action=action or _default_action_for_classification(classification),
        summary=str(classification_payload.get("summary") or "").strip(),
        requires_human_review=bool(
            classification_payload.get("requires_human_review", True)
        ),
        suspected_component=_mapping_payload(
            classification_payload.get("suspected_component")
        ),
        developer_note=developer_note,
        validation_report=_mapping_payload(validation_report),
        workflow_source=workflow_source,
        original_source_hash=_sha256_text(original_source),
        workflow_source_hash=_sha256_text(workflow_source),
        artifact_hashes={
            path.name: _sha256_text(path.read_text(encoding="utf-8"))
            for path in (
                classification_path,
                note_path,
                report_path,
                workflow_path,
            )
        },
        provider_metadata=dict(provider_metadata or {}),
    )


def format_remediation_version_notes(
    *,
    source_run_id: UUID,
    remediation_id: UUID,
    classification: WorkflowRunRemediationClassification,
    agent_provider: str,
    summary: str,
    developer_note: str,
) -> str:
    """Return audit notes for a remediation-created workflow version."""
    note_summary = (
        developer_note.strip().splitlines()[0] if developer_note.strip() else ""
    )
    return "\n".join(
        [
            "Automated remediation via Orcheo Vibe",
            "",
            f"Source run: {source_run_id}",
            f"Remediation: {remediation_id}",
            f"Classification: {classification.value}",
            f"Agent: {agent_provider}",
            "",
            "Summary:",
            summary.strip() or "No summary provided.",
            "",
            "Human review:",
            note_summary or "Review the remediation artifacts.",
        ]
    )


def _settings_bool(
    settings: object | None,
    attr_path: str,
    env_key: str,
    default: bool,
) -> bool:
    value = settings_value(
        settings,
        attr_path=attr_path,
        env_key=env_key,
        default=default,
    )
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _settings_int(
    settings: object | None,
    attr_path: str,
    env_key: str,
    default: int,
) -> int:
    value = settings_value(
        settings,
        attr_path=attr_path,
        env_key=env_key,
        default=default,
    )
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _settings_float(
    settings: object | None,
    attr_path: str,
    env_key: str,
    default: float,
) -> float:
    value = settings_value(
        settings,
        attr_path=attr_path,
        env_key=env_key,
        default=default,
    )
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _redact_text(text: str) -> str:
    redacted = PRIVATE_KEY_PATTERN.sub(REDACTED, text)
    redacted = BEARER_PATTERN.sub(f"Bearer {REDACTED}", redacted)
    redacted = SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}={REDACTED}",
        redacted,
    )
    redacted = TOKEN_PATTERN.sub(REDACTED, redacted)
    return LONG_TOKEN_PATTERN.sub(REDACTED, redacted)


def _is_vault_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("[[") and stripped.endswith("]]")


def _normalize_error_message(message: str) -> str:
    normalized = _redact_text(message).lower()
    normalized = UUID_PATTERN.sub("<uuid>", normalized)
    normalized = NUMBER_PATTERN.sub("<number>", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized


async def _load_run_history(
    history_store: Any | None,
    workflow_id: UUID,
    run_id: UUID,
) -> dict[str, Any] | None:
    if history_store is None:
        return None
    try:
        history = await history_store.get_history(str(run_id))
    except RunHistoryNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to load history for run %s", run_id)
        return None
    if hasattr(history, "model_dump"):
        return history.model_dump(mode="json")
    return None


def _guess_failed_component(history_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(history_payload, Mapping):
        return None
    steps = history_payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    for step in reversed(steps):
        payload = step.get("payload") if isinstance(step, Mapping) else None
        if not isinstance(payload, Mapping):
            continue
        for key in ("node", "edge", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if len(payload) == 1:
            key = next(iter(payload.keys()))
            return str(key)
    return None


def _workflow_source(graph: Mapping[str, Any]) -> str | None:
    if not isinstance(graph, Mapping):
        return None
    source = graph.get("source")
    if isinstance(source, str) and source.strip():
        return source
    return None


def _candidate_workflow_source(candidate: WorkflowRunRemediation) -> str:
    source = candidate.context.get("workflow_source")
    if not isinstance(source, str) or not source.strip():
        msg = "Remediation candidate does not include workflow script source."
        raise ValueError(msg)
    return source


async def _count_active_workflow_runs(repository: WorkflowRepository) -> int:
    count = 0
    for workflow in await repository.list_workflows(include_archived=True):
        runs = await repository.list_runs_for_workflow(workflow.id, limit=20)
        count += sum(run.status is WorkflowRunStatus.RUNNING for run in runs)
    return count


def _celery_workflow_load(celery_app: Any) -> int:
    try:
        inspector = celery_app.control.inspect()
        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
    except Exception:
        logger.exception("Failed to inspect Celery workflow load")
        return 1
    return _count_execution_tasks(active) + _count_execution_tasks(reserved)


def _count_execution_tasks(tasks_by_worker: Mapping[str, Any]) -> int:
    count = 0
    for tasks in tasks_by_worker.values():
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, Mapping):
                continue
            name = str(task.get("name") or "")
            if name.endswith("execute_run"):
                count += 1
    return count


def _host_load_average() -> float | None:
    try:
        return float(os.getloadavg()[0])
    except (AttributeError, OSError):
        return None


def _materialize_workspace(
    workspace: Path,
    candidate: WorkflowRunRemediation,
    source: str,
) -> str:
    context = dict(candidate.context)
    history = context.pop("recent_run_history", None)
    per_run_runnable_config = context.get("per_run_runnable_config") or context.get(
        "run_runnable_config",
        {},
    )
    stored_runnable_config = context.get(
        "stored_version_runnable_config",
    ) or context.get("version_runnable_config", {})
    instructions = _instructions_text(candidate)
    (workspace / "workflow.py").write_text(source, encoding="utf-8")
    (workspace / "failure.json").write_text(
        json.dumps(context, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (workspace / "run_history.json").write_text(
        json.dumps(history or {}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (workspace / "per_run_runnable_config.json").write_text(
        json.dumps(per_run_runnable_config or {}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (workspace / "stored_runnable_config.json").write_text(
        json.dumps(stored_runnable_config or {}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (workspace / "instructions.md").write_text(instructions, encoding="utf-8")
    return _sha256_text(instructions)


async def _invoke_orcheo_vibe(
    workspace: Path,
    candidate: WorkflowRunRemediation,
    settings: WorkflowAutofixSettings,
) -> dict[str, Any]:
    """Invoke the configured CLI coding agent through the Orcheo runtime path."""
    manager = ExternalAgentRuntimeManager()
    resolution = await manager.resolve_runtime(settings.agent_provider)
    provider = manager.get_provider(settings.agent_provider)
    provider_environment = manager.environment_for_provider(settings.agent_provider)
    probe = provider.probe_auth(resolution.runtime, environ=provider_environment)
    if not probe.authenticated:
        msg = probe.message or f"{settings.agent_provider} is not authenticated."
        raise RuntimeError(msg)
    prompt = (workspace / "instructions.md").read_text(encoding="utf-8")
    command = provider.build_command(
        resolution.runtime,
        prompt=prompt,
        system_prompt=(
            "You are Orcheo Vibe remediation. Work only inside the supplied "
            "temporary workspace and write the requested artifact files."
        ),
    )
    result = await execute_process(
        command,
        cwd=workspace,
        env=provider.build_environment(provider_environment),
        timeout_seconds=settings.agent_timeout_seconds,
    )
    metadata: dict[str, Any] = {
        "provider": settings.agent_provider,
        "runtime_version": resolution.runtime.version,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_seconds": result.duration_seconds,
        "stdout_sha256": _sha256_text(result.stdout),
        "stderr_sha256": _sha256_text(result.stderr),
    }
    audit = provider.execution_audit_metadata(
        resolution.runtime,
        command=command,
        working_directory=workspace,
    )
    if audit is not None:
        metadata["execution_audit"] = audit
    if result.exit_code != 0:
        raise RuntimeError(
            f"Orcheo Vibe remediation command failed with exit code {result.exit_code}"
        )
    del candidate
    return metadata


def _instructions_text(candidate: WorkflowRunRemediation) -> str:
    classification_values = " | ".join(
        item.value for item in WorkflowRunRemediationClassification
    )
    kind_values = " | ".join(
        [
            "workflow",
            "core_node",
            "plugin_node",
            "core_edge",
            "plugin_edge",
            "runtime",
            "external_dependency",
            "unknown",
        ]
    )
    return "\n".join(
        [
            "# Orcheo Vibe Workflow Remediation",
            "",
            "You are remediating one failed Orcheo workflow run.",
            "",
            "Read:",
            "- workflow.py: failed workflow version source.",
            "- failure.json: redacted failure context.",
            "- run_history.json: redacted recent execution history.",
            "",
            "Write all required artifacts before finishing:",
            "- classification.json",
            "- developer_note.md",
            "- validation_report.json",
            "- workflow.py",
            "",
            "classification.json must match this JSON shape:",
            "{",
            f'  "classification": "{classification_values}",',
            '  "confidence": 0.0,',
            '  "suspected_component": {',
            f'    "kind": "{kind_values}",',
            '    "name": "string | null",',
            '    "evidence": ["string"]',
            "  },",
            '  "action": "create_workflow_version | note_only",',
            '  "summary": "string",',
            '  "requires_human_review": true',
            "}",
            "",
            "Rules:",
            "- Classify before changing source.",
            "- Only workflow_fixable and node_or_edge_bug_workaround may edit source.",
            "- Work around predefined node/edge defects with workflow-local code.",
            "- Write a developer note naming suspected predefined components.",
            "- Note-only classifications must not change workflow.py.",
            "- Never edit core, plugins, credentials, env files, or other files.",
            "",
            f"Remediation id: {candidate.id}",
            f"Failed run id: {candidate.run_id}",
            "",
        ]
    )


def _artifact_payload(
    artifacts: RemediationArtifacts,
    *,
    prompt_hash: str,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "prompt_hash": prompt_hash,
        "artifact_hashes": artifacts.artifact_hashes,
        "agent_provider_metadata": artifacts.provider_metadata,
        "agent_validation_report": artifacts.validation_report,
        "summary": artifacts.summary,
        "confidence": artifacts.confidence,
        "suspected_component": artifacts.suspected_component,
        "requires_human_review": artifacts.requires_human_review,
        "source_changed": artifacts.source_changed,
        "dry_run": dry_run,
    }


async def _retry_fixed_workflow_run(
    *,
    repository: WorkflowRepository,
    candidate: WorkflowRunRemediation,
    created_version_id: UUID,
) -> dict[str, Any]:
    """Create and enqueue a best-effort retry run for a fixed workflow version."""
    try:
        source_run = await repository.get_run(candidate.run_id)
        retry_run = await repository.create_run(
            candidate.workflow_id,
            workflow_version_id=created_version_id,
            triggered_by=AUTOFIX_ACTOR,
            input_payload=source_run.input_payload,
            actor=AUTOFIX_ACTOR,
            runnable_config=source_run.runnable_config,
        )
    except Exception as exc:
        logger.exception(
            "Workflow remediation retry run creation failed remediation_id=%s",
            candidate.id,
        )
        return {"enabled": True, "created": False, "error": str(exc)}

    try:
        from orcheo_backend.worker.tasks import execute_run

        execute_run.delay(str(retry_run.id))
        logger.info(
            "Workflow remediation retry run enqueued remediation_id=%s run_id=%s",
            candidate.id,
            retry_run.id,
        )
        return {
            "enabled": True,
            "created": True,
            "run_id": str(retry_run.id),
            "enqueued": True,
        }
    except Exception as exc:
        logger.warning(
            "Workflow remediation retry run enqueue failed remediation_id=%s "
            "run_id=%s error=%s",
            candidate.id,
            retry_run.id,
            exc,
        )
        return {
            "enabled": True,
            "created": True,
            "run_id": str(retry_run.id),
            "enqueued": False,
            "error": str(exc),
        }


def _validate_workspace_artifact_boundary(workspace: Path) -> None:
    unexpected: list[str] = []
    for path in workspace.rglob("*"):
        if path.is_dir():
            continue
        relative_path = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            unexpected.append(relative_path)
            continue
        if relative_path.startswith("__pycache__/") and relative_path.endswith(".pyc"):
            continue
        if relative_path not in ALLOWED_WORKSPACE_FILES:
            unexpected.append(relative_path)
    if unexpected:
        unexpected_text = ", ".join(sorted(unexpected))
        msg = f"Unexpected remediation artifact(s): {unexpected_text}"
        raise ValueError(msg)


def _default_action_for_classification(
    classification: WorkflowRunRemediationClassification,
) -> str:
    if classification.creates_workflow_version:
        return "create_workflow_version"
    return "note_only"


def _mapping_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cleanup_workspace(path: Path) -> None:
    """Best-effort workspace cleanup hook for tests and future archive modes."""
    shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "AUTOFIX_ACTOR",
    "IdleDecision",
    "RemediationArtifacts",
    "WorkflowAutofixSettings",
    "attempt_workflow_remediation_async",
    "compute_error_fingerprint",
    "create_candidate_for_failed_run",
    "evaluate_remediation_idle",
    "format_remediation_version_notes",
    "load_workflow_autofix_settings",
    "parse_remediation_artifacts",
    "redact_sensitive_values",
    "scan_workflow_remediations_async",
]
