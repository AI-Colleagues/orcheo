# Project Plan

## For Proactive Workflow Error Remediation with Orcheo Vibe

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

Implement a conservative backend remediation loop that captures uncaught workflow run failures, waits for idle worker capacity, invokes Orcheo Vibe to classify the issue, and then either creates a validated workflow version or records a developer note. Automatic edits are limited to the failed workflow version's script source; core, plugin, and runtime issues are routed to human developers through notes.

**Related Documents:**
- Requirements: `project/initiatives/workflow_autofix_remediation/1_requirements.md`
- Design: `project/initiatives/workflow_autofix_remediation/2_design.md`

---

## Milestones

### Milestone 1: Remediation Candidate Persistence

**Description:** Add the data model and repository operations needed to store remediation candidates and audit their lifecycle. Success means failed-run context can be persisted, deduplicated, claimed, and updated without invoking an agent.

#### Task Checklist

- [ ] Task 1.1: Define `WorkflowRunRemediation` model, status enum, classification enum, and action enum
  - Dependencies: None
- [ ] Task 1.2: Extend repository protocol with remediation create, claim, update, list, and dismiss methods
  - Dependencies: Task 1.1
- [ ] Task 1.3: Implement in-memory repository support
  - Dependencies: Task 1.2
- [ ] Task 1.4: Implement SQLite schema and persistence support
  - Dependencies: Task 1.2
- [ ] Task 1.5: Implement PostgreSQL schema and persistence support if active repository parity requires it
  - Dependencies: Task 1.2
- [ ] Task 1.6: Add active-candidate deduplication by fingerprint, version checksum storage, and attempt-count tracking
  - Dependencies: Tasks 1.3-1.5
- [ ] Task 1.7: Add repository tests for create, duplicate suppression, atomic claim, terminal updates, list filters, and dismiss
  - Dependencies: Tasks 1.3-1.6

---

### Milestone 2: Failure Capture and Redaction

**Description:** Capture enough structured context from uncaught workflow failures to support later remediation while preventing secret leakage. Success means failed worker runs create safe pending candidates with useful context.

#### Task Checklist

- [ ] Task 2.1: Add error fingerprinting helper for workflow version checksum, exception type, normalized message, phase, and failed node/edge when available
  - Dependencies: Milestone 1
- [ ] Task 2.2: Add redaction utilities for inputs, stored and per-run runnable config, traceback text, history payloads, stdout/stderr, headers, vault resolutions, and token-like strings
  - Dependencies: Milestone 1
- [ ] Task 2.3: Extend worker failure handling to collect failed version script source, graph format, run metadata, exception details, stored and per-run runnable config, and recent run history
  - Dependencies: Tasks 2.1-2.2
- [ ] Task 2.4: Create remediation candidates after failed-run persistence succeeds
  - Dependencies: Task 2.3
- [ ] Task 2.5: Ensure candidate creation failures are logged but never mask the original workflow run failure
  - Dependencies: Task 2.4
- [ ] Task 2.6: Add tests for redaction coverage, fingerprint stability, candidate creation, and candidate creation failure tolerance
  - Dependencies: Tasks 2.1-2.5

---

### Milestone 3: Idle Supervisor and Feature Flags

**Description:** Add the background scanner that only starts remediation when normal workflow execution has priority. Success means candidates remain pending while the worker is busy and are claimed only under idle conditions.

#### Task Checklist

- [ ] Task 3.1: Add settings for `ORCHEO_WORKFLOW_AUTOFIX_ENABLED`, max concurrent attempts, idle load threshold, and dry-run mode
  - Dependencies: Milestone 1
- [ ] Task 3.2: Implement idle checks for active workflow runs, Celery active/reserved workflow execution tasks, host load, and unknown-host-load behavior
  - Dependencies: Task 3.1
- [ ] Task 3.3: Add Celery task `scan_workflow_remediations`
  - Dependencies: Tasks 3.1-3.2
- [ ] Task 3.4: Add Celery task `attempt_workflow_remediation`
  - Dependencies: Task 3.3
- [ ] Task 3.5: Enforce global remediation concurrency of one by default
  - Dependencies: Task 3.4
- [ ] Task 3.6: Add tests for disabled flag, busy skip, idle claim, concurrency limit, and attempt cap behavior
  - Dependencies: Tasks 3.1-3.5

---

### Milestone 4: Orcheo Vibe Remediation Runner

**Description:** Invoke Orcheo Vibe with a strict artifact contract and classify failures before any workflow source changes. Success means the backend can parse agent outputs and distinguish fixable, workaround, and note-only paths.

#### Task Checklist

- [ ] Task 4.1: Define remediation prompt template and instructions for Orcheo Vibe
  - Dependencies: Milestones 1-3
- [ ] Task 4.2: Materialize temporary remediation workspace with `workflow.py`, `failure.json`, `run_history.json`, runnable-config artifacts, and `instructions.md`
  - Dependencies: Task 4.1
- [ ] Task 4.3: Invoke Orcheo Vibe through the existing CLI agent integration path
  - Dependencies: Task 4.2
- [ ] Task 4.4: Parse `classification.json`, `developer_note.md`, `validation_report.json`, and optional edited `workflow.py`
  - Dependencies: Task 4.3
- [ ] Task 4.5: Validate artifact boundaries so only workflow source can be changed automatically, and note-only classifications cannot change source
  - Dependencies: Task 4.4
- [ ] Task 4.6: Store prompt hash, artifact hashes, provider metadata, and raw validation summaries
  - Dependencies: Tasks 4.3-4.5
- [ ] Task 4.7: Add tests using fake Orcheo Vibe outputs for each classification path
  - Dependencies: Tasks 4.1-4.6

---

### Milestone 5: Workflow Version Creation and Note-only Outcomes

**Description:** Convert validated agent artifacts into durable Orcheo outcomes. Success means workflow-fixable attempts create new workflow versions, while runtime/platform issues produce developer notes only.

#### Task Checklist

- [ ] Task 5.1: Validate edited workflow source through the existing LangGraph script ingestion/build path
  - Dependencies: Milestone 4
- [ ] Task 5.2: Create new workflow versions for `workflow_fixable` outputs from ingested graph payloads, preserving intended runnable config and adding remediation metadata
  - Dependencies: Task 5.1
- [ ] Task 5.3: Create new workflow versions plus developer notes for `node_or_edge_bug_workaround` outputs
  - Dependencies: Task 5.1
- [ ] Task 5.4: Store note-only remediations for `runtime_or_platform`, `external_dependency`, and `unknown`, rejecting or ignoring changed source artifacts
  - Dependencies: Milestone 4
- [ ] Task 5.5: Mark invalid workflow-source outputs as failed remediations without creating versions
  - Dependencies: Task 5.1
- [ ] Task 5.6: Add optional retry-after-fix setting, disabled by default
  - Dependencies: Tasks 5.2-5.3
- [ ] Task 5.7: Add integration tests for version creation, workaround notes, note-only outcomes, invalid source, and disabled retry behavior
  - Dependencies: Tasks 5.1-5.6

---

### Milestone 6: API, Canvas Visibility, and Operations

**Description:** Expose remediation state to users and operators after the backend path is safe. Success means failed run views can show whether Orcheo tried remediation and what outcome it produced.

#### Task Checklist

- [ ] Task 6.1: Add read APIs for remediation candidates by run, workflow, version, status, and candidate id
  - Dependencies: Milestone 5
- [ ] Task 6.2: Add dismiss API for human-reviewed candidates
  - Dependencies: Task 6.1
- [ ] Task 6.3: Add Canvas failed-run remediation summary
  - Dependencies: Task 6.1
- [ ] Task 6.4: Add Canvas developer note and created version links
  - Dependencies: Task 6.3
- [ ] Task 6.5: Document feature flags, idle behavior, safety boundaries, and remediation statuses
  - Dependencies: Milestone 5
- [ ] Task 6.6: Add operational metrics/logging for created candidates, claimed candidates, fixed versions, note-only results, failures, and skipped scans
  - Dependencies: Milestone 5
- [ ] Task 6.7: Run `make format`, `make lint`, focused backend tests, and Canvas tests for new UI surfaces
  - Dependencies: Tasks 6.1-6.6

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Codex | Initial draft |
