# Design Document

## For Proactive Workflow Error Remediation with Orcheo Vibe

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

This design adds a conservative remediation loop for failed Orcheo workflow runs. When a worker-executed workflow fails with an uncaught exception, Orcheo stores a structured remediation candidate with redacted context. A background supervisor later claims candidates only when the machine is idle and asks Orcheo Vibe to classify the issue and produce either a workflow-local patch or a developer note.

The central safety boundary is ownership. Automatic remediation can create new workflow versions, because workflow source is the user-owned artifact. It cannot patch Orcheo core code or plugin package code. If the failure appears to come from a predefined core/plugin node or edge, Orcheo Vibe may define a custom workflow-local node or edge that works around the behavior, and it must also record a developer note for human review. All edits are based on the failed workflow version's script source, not whichever version is latest when the remediation attempt runs.

## Components

- **Remediation Candidate Store (Backend repository)**
  - Persists failed-run remediation candidates, status transitions, classification, notes, artifact metadata, and created workflow version ids.
  - Implemented behind repository protocol methods with SQLite/PostgreSQL/in-memory parity where applicable.

- **Failure Capture Hook (`apps/backend/src/orcheo_backend/worker/tasks.py`)**
  - Extends the run failure path after `mark_run_failed`.
  - Extracts exception metadata, traceback, run context, run history, stored version runnable config, per-run runnable config, graph format, and failed version script source.
  - Redacts sensitive values before candidate persistence.

- **Idle Remediation Supervisor (Celery task)**
  - Periodically scans for pending candidates.
  - Checks active worker load before claiming work.
  - Enqueues or directly executes one remediation attempt at a time.

- **Orcheo Vibe Remediation Runner**
  - Invokes Orcheo Vibe with a structured task and artifact contract.
  - Uses existing CLI agent integrations internally.
  - Runs outside the failed workflow graph to avoid recursion.

- **Temporary Remediation Workspace**
  - Contains `workflow.py`, `failure.json`, `run_history.json`, `instructions.md`, and expected output paths.
  - Is deleted or archived according to audit settings after the attempt.

- **Workflow Version Validator**
  - Uses the existing script ingestion/build path to validate edited workflow source.
  - Creates a new workflow version only after validation succeeds.

- **Canvas Remediation Views (P1)**
  - Surfaces remediation status, classification, created version links, and developer notes on failed run and workflow pages.

## Request Flows

### Flow 1: Failed run creates a remediation candidate

1. Worker execution catches an uncaught exception during workflow execution.
2. Existing failure handling marks the run failed and records run history failure state.
3. Failure capture builds a redacted context package:
   - workflow id
   - workflow version id
   - run id
   - version checksum
   - graph format
   - exception type
   - normalized error message
   - traceback
   - recent history steps
   - inputs, stored version runnable config, and per-run runnable config
   - failed version script source when available
4. Backend computes an error fingerprint.
5. Repository creates a pending remediation candidate unless an active candidate already exists for the fingerprint.
6. Candidate creation errors are logged and never change the original failed-run outcome.

### Flow 2: Idle supervisor claims work

1. Celery Beat invokes `scan_workflow_remediations`.
2. The scanner checks whether automatic remediation is enabled.
3. The scanner checks idle gates:
   - no active remediation attempt
   - active workflow run count below threshold
   - Celery active/reserved workflow execution tasks below threshold
   - host load below threshold when available, or the configured unknown-load policy allows remediation
4. If idle, scanner claims one pending candidate using an atomic repository transition.
5. Scanner starts `attempt_workflow_remediation(candidate_id)`.

### Flow 3: Workflow-level fix

1. Runner materializes the temporary workspace.
2. Orcheo Vibe receives the context and must first write `classification.json`.
3. Classification is `workflow_fixable` or `node_or_edge_bug_workaround`.
4. Orcheo Vibe edits `workflow.py` and writes:
   - `developer_note.md`
   - agent-side `validation_report.json`
   - patch summary metadata
5. Backend validates `workflow.py` through LangGraph script ingestion/build.
6. Backend creates a new workflow version from the ingested graph payload, preserving intended runnable config and adding remediation metadata and notes.
7. Candidate status becomes `fixed`, with `created_version_id` set.

### Flow 4: Predefined node or edge workaround

1. Orcheo Vibe identifies a suspected predefined core/plugin node or edge defect.
2. It leaves Orcheo core/plugin code untouched.
3. It defines a workflow-local custom `TaskNode` or `BaseEdge` in `workflow.py`.
4. It replaces or wraps the failing predefined node/edge in the graph assembly.
5. It writes a developer note naming the suspected predefined component, evidence, and recommended human follow-up.
6. Backend validates and creates a new workflow version if the script is valid.

### Flow 5: Note-only remediation

1. Classification is `runtime_or_platform`, `external_dependency`, or `unknown`.
2. Orcheo Vibe does not edit workflow source.
3. It writes `developer_note.md` with reproduction context, likely owner, impact, and next action.
4. Backend verifies the workflow source is unchanged or ignores the emitted source artifact.
5. Backend stores the note and marks the candidate `note_only`.

## API Contracts

### Internal repository methods

```python
async def create_remediation_candidate(
    *,
    workflow_id: UUID,
    workflow_version_id: UUID,
    run_id: UUID,
    fingerprint: str,
    version_checksum: str,
    graph_format: str | None,
    context: dict[str, Any],
) -> WorkflowRunRemediation: ...

async def claim_next_remediation_candidate(
    *,
    actor: str,
    now: datetime | None = None,
) -> WorkflowRunRemediation | None: ...

async def get_remediation_candidate(
    remediation_id: UUID,
) -> WorkflowRunRemediation: ...

async def list_remediation_candidates(
    *,
    workflow_id: UUID | None = None,
    workflow_version_id: UUID | None = None,
    run_id: UUID | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[WorkflowRunRemediation]: ...

async def mark_remediation_fixed(
    remediation_id: UUID,
    *,
    created_version_id: UUID,
    classification: str,
    developer_note: str,
    artifacts: dict[str, Any],
    validation_result: dict[str, Any],
) -> WorkflowRunRemediation: ...

async def mark_remediation_note_only(
    remediation_id: UUID,
    *,
    classification: str,
    developer_note: str,
    artifacts: dict[str, Any],
) -> WorkflowRunRemediation: ...

async def dismiss_remediation_candidate(
    remediation_id: UUID,
    *,
    actor: str,
    reason: str | None = None,
) -> WorkflowRunRemediation: ...

async def mark_remediation_failed(
    remediation_id: UUID,
    *,
    error: str,
    artifacts: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
) -> WorkflowRunRemediation: ...
```

### Orcheo Vibe artifact contract

```json
{
  "classification": "workflow_fixable | node_or_edge_bug_workaround | runtime_or_platform | external_dependency | unknown",
  "confidence": 0.0,
  "suspected_component": {
    "kind": "workflow | core_node | plugin_node | core_edge | plugin_edge | runtime | external_dependency | unknown",
    "name": "string | null",
    "evidence": ["string"]
  },
  "action": "create_workflow_version | note_only",
  "summary": "string",
  "requires_human_review": true
}
```

Expected files:

| File | Required | Description |
|------|----------|-------------|
| `classification.json` | Yes | Structured classification and intended action |
| `workflow.py` | Original source for all script candidates; edited output only for fixes | Workflow source scoped to the failed version |
| `developer_note.md` | Yes | Human-readable explanation and follow-up |
| `validation_report.json` | Yes | Agent-side validation notes and commands attempted |

### Version notes format

```text
Automated remediation via Orcheo Vibe

Source run: <run_id>
Remediation: <remediation_id>
Classification: <classification>
Agent: <provider/version>

Summary:
<summary>

Human review:
<developer note summary>
```

## Data Models / Schemas

### `WorkflowRunRemediation`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Remediation candidate id |
| `workflow_id` | UUID | Affected workflow |
| `workflow_version_id` | UUID | Failed workflow version |
| `run_id` | UUID | Source failed run |
| `status` | string | `pending`, `claimed`, `fixed`, `note_only`, `failed`, `dismissed` |
| `fingerprint` | string | Deduplication key |
| `version_checksum` | string | Checksum of the failed workflow version graph payload |
| `graph_format` | string \| null | Failed version graph format, for script-vs-legacy diagnostics |
| `attempt_count` | integer | Number of attempts |
| `classification` | string \| null | Agent classification |
| `action` | string \| null | `create_workflow_version` or `note_only` |
| `context` | object | Redacted failure context |
| `developer_note` | string \| null | Human follow-up note |
| `created_version_id` | UUID \| null | New workflow version from a successful fix |
| `artifacts` | object | Prompt hash, output hashes, validation metadata |
| `validation_result` | object \| null | Backend-side ingestion/build validation result |
| `last_error` | string \| null | Last remediation runner or validation failure |
| `claimed_by` | string \| null | Worker or actor that claimed the candidate |
| `claimed_at` | datetime \| null | Claim timestamp |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### Error fingerprint inputs

| Input | Description |
|-------|-------------|
| Workflow version checksum | Separates old/new workflow source failures |
| Exception type | Preserves failure class |
| Normalized message | Reduces repeated literal-value noise |
| Failed node or edge | Included when trace/history identifies one |
| Runtime phase | Distinguishes build, execution, history, persistence, and cleanup failures |

## Security Considerations

- Candidate context and agent prompts must be redacted before persistence.
- Redaction should cover known credential fields, vault placeholder resolutions, token-like strings, bearer headers, cookies, private keys, and provider auth blobs.
- The remediation agent receives a temporary workspace, not the Orcheo repository.
- Automatic edits are restricted to the workflow script artifact.
- Core code, plugin package code, `.env`, vault files, runtime manifests, and worker config are not writable by the remediation task.
- Every created workflow version must include audit notes linking back to the failed run and remediation candidate.
- Automatic retry-after-fix remains disabled by default.

## Performance Considerations

- Candidate creation must be lightweight and should not significantly slow failure handling.
- The idle scanner should read a small bounded number of pending candidates per cycle.
- Agent execution is expensive and must be concurrency-limited.
- Host load checks should degrade conservatively: if load cannot be inspected, apply the configured unknown-load policy and default to skipping remediation.
- Temporary workspaces should be cleaned after successful artifact persistence to avoid disk growth.

## Testing Strategy

- **Unit tests:** Error fingerprinting, redaction, candidate deduplication, status transitions, idle gate decisions, classification parsing, artifact validation, and version-note formatting.
- **Repository tests:** SQLite/PostgreSQL/in-memory candidate creation, claim atomicity, duplicate suppression, and status updates.
- **Worker tests:** Failed run creates candidate, scanner skips when busy, scanner claims when idle, attempt limit enforcement, and cleanup behavior.
- **Integration tests:** Workflow-fixable artifact creates a new version, predefined node workaround creates a version plus note, runtime/platform classification stores note-only, invalid workflow source marks remediation failed.
- **Manual QA checklist:** Trigger a failing workflow, verify candidate visibility, run dry-run note-only mode, run workflow fix mode, inspect created version, verify no core/plugin files are modified, and confirm normal workflow queue latency is unchanged.

## Rollout Plan

1. Phase 1: Add persistence, redaction, candidate capture, and dry-run note-only agent invocation behind a disabled default flag.
2. Phase 2: Enable workflow-version creation for selected self-hosted workflows with manual review.
3. Phase 3: Add Canvas surfaces, dismissal controls, and optional retry-after-fix.

Backwards compatibility notes:
- Existing workflow run status semantics remain unchanged.
- Failed runs remain immutable.
- New repository methods and tables are additive.
- Automatic remediation is disabled by default.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Codex | Initial draft |
