# Project Plan

## For Python-Only Workflow Composition

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-07
- **Status:** Approved

---

## Overview

Execute a coordinated refactor to make Python LangGraph ingestion the only supported workflow composition path, remove JSON composition paths and the Orcheo MCP SDK server (`packages/sdk/src/orcheo_sdk/mcp_server/`) from active runtime/tooling, and archive removed code under `legacy/`.

**Related Documents:**
- Requirements: `project/initiatives/python_only_workflow_composition/1_requirements.md`
- Design: `project/initiatives/python_only_workflow_composition/2_design.md`

---

## Milestones

### Milestone 1: Backend and Runtime Consolidation

**Description:** Remove JSON composition execution and creation surfaces while preserving Python ingest and version-level runnable config.

#### Task Checklist

- [x] Task 1.1: Remove direct JSON workflow version creation route (`POST /workflows/{ref}/versions`)
  - Dependencies: None
- [x] Task 1.2: Keep `/versions/ingest` as sole version-creation path and validate script payloads as current
  - Dependencies: Task 1.1
- [x] Task 1.3: Restrict runtime graph build/execution to `langgraph-script` only; add explicit unsupported-format errors for legacy graph payloads
  - Dependencies: Task 1.2
- [x] Task 1.4: Add/enable version runnable-config update endpoint for config-only persistence
  - Dependencies: Task 1.2

---

### Milestone 2: Studio + CLI/SDK Behavior Alignment

**Description:** Align client tooling with Python-only composition and config-only Studio persistence.

#### Task Checklist

- [x] Task 2.1: Update Studio save flow to stop creating versions and write config-only updates to backend version `runnable_config`
  - Dependencies: Milestone 1
- [x] Task 2.2: Remove CLI/SDK JSON upload path (`.json` workflow files)
  - Dependencies: Milestone 1
- [x] Task 2.3: Remove CLI/SDK JSON download/export path and keep Python output only
  - Dependencies: Task 2.2
- [x] Task 2.4: Add CLI config-only save flow to update version `runnable_config` without creating a version
  - Dependencies: Task 1.4
- [x] Task 2.5: Update command help/docs/messages for removed JSON support and new config-only save behavior
  - Dependencies: Task 2.2

---

### Milestone 3: Orcheo MCP SDK Server Removal, Legacy Archive, and Validation

**Description:** Remove the Orcheo MCP SDK server module (`orcheo_sdk.mcp_server`) from active runtime, archive removed code, and complete verification.

#### Task Checklist

- [x] Task 3.1: Remove the `orcheo_sdk.mcp_server` module and its tool registrations/wrappers from active runtime exports
  - Dependencies: Milestone 2
- [x] Task 3.2: Move removed implementation/tests/docs to `legacy/` and exclude from lint/test/package runtime
  - Dependencies: Task 3.1
- [x] Task 3.3: Add/adjust tests for Python-only ingest, config-only save, and legacy unsupported-format failures
  - Dependencies: Milestone 1, Milestone 2
- [x] Task 3.4: Publish release notes and migration guidance (re-ingest Python for any legacy JSON workflows)
  - Dependencies: Task 3.3

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-07 | Codex | Initial draft |

---

## Rollback / Contingency

- If deployment issues arise, retain Python ingest path and temporarily gate only client-facing removals while preserving runtime safety checks.
- Do not restore JSON composition support in active runtime; use explicit error handling and documented remediation instead.
