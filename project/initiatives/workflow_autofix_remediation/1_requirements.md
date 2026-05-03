# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Proactive Workflow Error Remediation with Orcheo Vibe
- **Type:** Enhancement
- **Summary:** Enable Orcheo to create remediation candidates for uncaught workflow run failures, then use the Orcheo Vibe agent during idle worker capacity to either create workflow-level fixes or record developer action notes. Workflow-level fixes operate on the failed workflow version's script source and may replace problematic predefined core/plugin nodes or edges with custom workflow-local nodes or edges, while runtime/platform issues remain note-only for human follow-up.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-05-03

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| External Agent CLI Nodes | `project/initiatives/external_agent_cli_nodes/1_requirements.md` | ShaojieJiang | CLI agent runtime foundation |
| Execution Worker Initiative | `project/initiatives/execution_worker/1_requirements.md` | ShaojieJiang | Worker execution model |
| Python-only Workflow Composition | `project/initiatives/python_only_workflow_composition/1_requirements.md` | ShaojieJiang | Script workflow versioning model |
| Workflow builder | `src/orcheo/graph/builder.py` | Platform | Runtime graph loading |
| Worker tasks | `apps/backend/src/orcheo_backend/worker/tasks.py` | Platform | Workflow failure capture |

## PROBLEM DEFINITION

### Objectives
Reduce unattended workflow failure time by allowing Orcheo to opportunistically diagnose failed runs and propose or create workflow-level remediations when the worker machine is idle. Keep automatic changes bounded to workflow versions, and route runtime/platform issues to human developers as structured notes.

### Target users
- Workflow authors who want recurring or unattended workflows to recover from fixable graph-level failures.
- Human developers responsible for maintaining core nodes, plugin nodes, plugin edges, and runtime infrastructure.
- Self-hosted Orcheo operators who already connect Orcheo Vibe to CLI coding agents.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Workflow author | Have Orcheo notice uncaught workflow run failures | I do not need to manually inspect every failed unattended run | P0 | Failed runs create deduplicated remediation candidates with error, failed version, input, runnable config, and history context |
| Workflow author | Let Orcheo fix workflow-local issues when safe | My workflow can recover without waiting for a core platform release | P0 | A validated new workflow version is created only when the fix is limited to workflow source |
| Workflow author | Work around broken predefined nodes or edges with custom workflow-local code | Core/plugin defects do not block my workflow immediately | P0 | The remediation replaces or wraps suspected predefined nodes/edges using custom nodes/edges inside the workflow script |
| Human developer | Receive notes for suspected core/plugin defects | I can later decide whether the workaround should become a platform fix | P0 | Remediation notes name the suspected node/edge, evidence, workaround, and follow-up recommendation |
| Human developer | Receive notes for runtime/platform failures | I can act on issues that an agent cannot safely fix in a workflow | P0 | Runtime, persistence, credential, sandbox, Celery, LangGraph, and infrastructure failures produce note-only remediations |
| Operator | Run remediation only when the machine is not busy | Normal workflow execution remains the priority | P0 | The scanner skips work when normal worker queues, active workflow runs, or host load exceed configured thresholds |
| Platform team | Prevent repeated or unsafe autofix attempts | Failed remediations do not create loops or uncontrolled version churn | P0 | Attempts are deduplicated by fingerprint and capped per workflow version |
| Reviewer | Audit every automated change | I can inspect what the agent saw and changed | P0 | Each remediation stores prompt metadata, classification, artifacts, validation result, and created version id |
| Canvas user | See remediation status on failed runs | I can tell whether Orcheo tried to help and what happened | P1 | Run detail surfaces candidate status, fix summary, created version, and developer note |

### Context, Problems, Opportunities

Orcheo workflows increasingly use AI agents, plugins, and custom graph scripts. Failures can occur in three broad places: workflow-authored logic, predefined core/plugin nodes or edges, and platform/runtime infrastructure. Today uncaught workflow run errors are marked failed, but the system does not preserve enough structured context to automatically classify or remediate them.

The new CLI agent integrations and Orcheo Vibe make it possible to inspect failed workflow context and produce code changes. The opportunity is to turn idle worker capacity into a conservative remediation loop that fixes only workflow-owned source, while creating useful developer notes when the problem belongs in core, plugins, or runtime infrastructure.

### Product goals and Non-goals

**Goals:**
- Capture structured remediation candidates for uncaught workflow run failures.
- Use Orcheo Vibe as the product-level remediation agent integration.
- Run remediation only during idle worker capacity and with strict concurrency limits.
- Classify failures before attempting any fix.
- Create new workflow versions for workflow-fixable issues.
- Work around suspected predefined core/plugin node or edge defects by defining custom workflow-local nodes or edges.
- Record developer notes for all suspected core/plugin defects, including those with workflow-level workarounds.
- Record note-only remediations for runtime/platform failures or issues not safely fixable within workflow source.
- Keep failed runs immutable and audit all remediation attempts.

**Non-goals:**
- Automatically patch Orcheo core source code.
- Automatically patch installed plugin package source code.
- Automatically commit to the Orcheo repository or plugin repositories.
- Guarantee semantic correctness of every agent-produced workflow fix.
- Run remediation while the worker is already busy with normal workflow execution.
- Provide hosted multi-tenant automatic remediation in the first release.
- Automatically reroute secrets, credentials, or vault placeholders unless the workflow source already defines that pattern.

## PRODUCT DEFINITION

### Requirements

**P0: Failure capture and candidate creation**
- Extend failed workflow execution handling to create a remediation candidate when a run fails with an uncaught exception.
- Candidate context must include workflow id, workflow version id, run id, version checksum, graph format, exception type, error message, traceback, inputs, per-run runnable config, stored version runnable config, recent run history, and the failed version's script source when available.
- Sensitive values must be redacted before persistence or agent prompting.
- Candidate creation must be best-effort and must not mask or alter the original failed-run persistence path.

**P0: Deduplication and attempt limits**
- Compute an error fingerprint using workflow version checksum, exception type, normalized message, and likely failing node/edge when known.
- Do not create duplicate active candidates for the same fingerprint.
- Cap remediation attempts per fingerprint and per workflow version.
- Terminal candidates must remain queryable for audit even when a later equivalent failure creates a new candidate after attempt caps or dismissal rules allow it.

**P0: Idle supervisor**
- Add a backend/worker scanner that claims pending remediation candidates only when the machine is not busy.
- Idle checks must consider active workflow execution tasks, reserved workflow execution tasks, running workflow runs, and host load.
- Idle checks must not treat the remediation scanner task itself as normal workflow load.
- Remediation execution must be globally constrained, with a default concurrency of one.

**P0: Orcheo Vibe remediation invocation**
- Invoke Orcheo Vibe as the remediation agent, using the existing CLI agent integrations underneath.
- The prompt contract must require classification before code changes.
- The agent works in a temporary workspace containing redacted failure context, the failed version's workflow source, instructions, and output artifact paths.

**P0: Classification**
- The agent must classify each candidate as one of:
  - `workflow_fixable`
  - `node_or_edge_bug_workaround`
  - `runtime_or_platform`
  - `external_dependency`
  - `unknown`
- Only `workflow_fixable` and `node_or_edge_bug_workaround` can create a new workflow version.
- `runtime_or_platform`, `external_dependency`, and `unknown` are note-only in V1.

**P0: Workflow-level fix path**
- The agent may edit only the workflow script.
- The backend must validate the edited script through the existing LangGraph script ingestion/build path before creating a version.
- Validation must ingest the edited script into a graph payload and create the new version through repository version creation, preserving intended runnable config and attaching remediation metadata.
- Successful fixes create a new workflow version with notes referencing the failed run, remediation id, classification, agent provider, and summary.
- The original failed run remains unchanged.

**P0: Predefined node/edge workaround path**
- When a predefined core/plugin node or edge appears to be the cause, the agent must not patch the core or plugin implementation.
- The agent may define a custom workflow-local `TaskNode` or `BaseEdge` in the workflow script and replace or wrap the problematic predefined node/edge.
- The remediation must include a developer note requesting human review of the suspected predefined node/edge.

**P0: Note-only path**
- Runtime/platform failures must create developer notes rather than workflow versions.
- Notes must include reproduction context, suspected owner, evidence, affected workflow/run, and recommended next action.
- Note-only candidates must be visible from failed run and workflow views.
- Note-only remediation must not alter the workflow script; if the agent emits a changed script for a note-only action, the backend must ignore or reject that artifact.

**P0: Validation and audit**
- Every agent result must include structured artifacts: classification, patch summary, developer note, validation report, and optional edited workflow source.
- Store the agent provider, runtime metadata, prompt hash, artifact hashes, and validation result.
- Reject artifacts that modify files outside the allowed temporary workflow source.
- Store backend validation output separately from any agent-side validation report.

**P1: Retry-after-fix**
- Optionally trigger a retry run against the newly created workflow version after validation succeeds.
- Retry must be opt-in or feature-flagged until enough confidence is established.

**P1: Canvas visibility**
- Add remediation status to failed run detail views.
- Link created workflow versions and display developer notes.
- Support manual dismiss and manual rerun actions.

### Designs (if applicable)
- Design document: `project/initiatives/workflow_autofix_remediation/2_design.md`

### Other Teams Impacted
- **Execution Worker:** Gains idle remediation scanning and agent invocation responsibilities.
- **Workflow Repository:** Stores remediation candidates, notes, and audit artifacts.
- **Canvas Frontend:** Surfaces remediation status, notes, and created versions.
- **Plugin Maintainers:** Receive structured notes for suspected predefined node/edge defects.

## TECHNICAL CONSIDERATIONS

### Architecture Overview
This initiative fits behind the existing workflow run failure path. Failed runs create remediation candidates; an idle worker scanner claims candidates; Orcheo Vibe analyzes the context and emits structured artifacts; the backend validates and persists either a new workflow version or a developer note.

### Technical Requirements
- Add persistence support for remediation candidates in in-memory, SQLite, and PostgreSQL repositories to match active workflow repository implementations.
- Add repository protocol methods for creating, claiming, updating, listing, and dismissing remediation candidates.
- Use existing LangGraph script ingestion/build validation for edited workflow scripts.
- Redact secrets from all persisted context and agent prompts.
- Add feature flags for automatic remediation scanning and retry-after-fix.
- Keep remediation independent from normal workflow graph execution to avoid recursive workflows.
- Ensure cleanup of temporary workspaces and external agent processes after each attempt.

### AI/ML Considerations

#### Data Requirements
The agent receives redacted failed run data: workflow script, error details, traceback, run history, inputs, stored and per-run runnable config, and relevant metadata. No training data collection is required.

#### Algorithm selection
Use Orcheo Vibe to route to an available CLI coding agent provider. The backend enforces artifact contracts, validation, and allowed mutation boundaries rather than trusting free-form agent output.

#### Model performance requirements
The quality bar is conservative operational usefulness: high precision for workflow-local changes, no automatic platform/plugin source modifications, and useful note-only output for non-fixable issues.

## MARKET DEFINITION
This is an internal/self-hosted platform enhancement. External market sizing is out of scope.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Candidate creation coverage | >= 95% of uncaught failed worker runs create a remediation candidate with structured context |
| [Primary] Validated workflow fixes | >= 70% of accepted workflow-fixable attempts produce an ingestible new version in pilot workflows |
| [Secondary] Developer note usefulness | Human reviewers mark >= 80% of note-only remediations as actionable during pilot review |
| [Guardrail] Unsafe mutation incidents | Zero automatic edits to Orcheo core or plugin package source |
| [Guardrail] Worker contention | No measurable increase in normal workflow queue latency while remediation is enabled |

### Rollout Strategy
Ship behind `ORCHEO_WORKFLOW_AUTOFIX_ENABLED=false` by default. Start with candidate capture and note-only dry runs, then enable workflow-version creation for selected self-hosted workflows. Keep retry-after-fix disabled until pilot results show stable validation quality.

### Estimated Launch Phases

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal development | Persist candidates, redaction, classification prompt, and note-only output |
| **Phase 2** | Controlled self-hosted pilot | Enable workflow-version creation for selected workflows with manual review |
| **Phase 3** | Broader self-hosted availability | Add Canvas visibility, manual controls, and optional retry-after-fix |

## HYPOTHESIS & RISKS

**Hypothesis:** Many failed unattended workflow runs are caused by workflow-level logic or recoverable predefined node/edge behavior, and Orcheo Vibe can produce useful remediations when constrained to workflow source.

**Risk:** The agent may create a syntactically valid but semantically wrong workflow version. **Risk mitigation:** Validate ingestion/build, keep the original failed version immutable, require clear version notes, and keep retry-after-fix opt-in.

**Risk:** Automatic remediation could compete with normal workflow execution. **Risk mitigation:** Run only under idle checks, keep concurrency at one by default, and expose feature flags.

**Risk:** Failure context may leak secrets into prompts or persisted artifacts. **Risk mitigation:** Redact credentials, vault values, token-like strings, and configured sensitive keys before candidate persistence and prompting.

**Risk:** Workarounds for core/plugin defects may hide platform bugs. **Risk mitigation:** Require developer notes for every predefined node/edge workaround and expose them for human review.

## APPENDIX
- The feature name should remain user-facing as Orcheo Vibe remediation. Low-level CLI runtime managers are internal implementation details.
