# Project Plan

## For Stack Installation Simplification and Version Awareness

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-02-21
- **Status:** Approved

---

## Overview

Deliver a guided single-command setup/upgrade experience, plus version awareness and update reminders in Studio and CLI. The rollout prioritizes installation simplification first, then shared version metadata, then reminder UX behavior. Update reminders are stable-release-only, and development/private builds do not emit reminder prompts.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 1: Setup/upgrade command foundation

**Description:** Define and implement a guided installation/upgrade command with prompt defaults and non-interactive support.

#### Task Checklist

- [x] Task 1.1: Finalize command contract (`orcheo install`) and Unix bootstrap (`curl ... | sh`) UX
  - Dependencies: None
- [x] Task 1.2: Implement thin bootstrap handoff to `uvx orcheo-sdk install` with `uv` detection/install flow
  - Dependencies: Task 1.1
- [x] Task 1.3: Implement prerequisite checks and actionable error messaging
  - Dependencies: Task 1.1
- [x] Task 1.4: Implement guided prompts (install/upgrade mode, backend URL, auth mode, optional stack start)
  - Dependencies: Task 1.1
- [x] Task 1.4a: Implement secret prompt behavior defaults (auto-generate locally for eligible secrets, explicit manual-entry opt-out)
  - Dependencies: Task 1.4
- [x] Task 1.5: Implement Docker-missing decision flow (default install Docker, explicit skip for remote backend path)
  - Dependencies: Task 1.3
- [x] Task 1.6: Implement install/upgrade executors and idempotent reconciliation with config preservation
  - Dependencies: Task 1.1
- [x] Task 1.7: Add non-interactive flags (`--yes` and explicit overrides)
  - Dependencies: Task 1.4
- [x] Task 1.8: Add summary output with installed versions and next steps
  - Dependencies: Task 1.6
- [x] Task 1.9: Provision stack compose assets from canonical repo path (`deploy/stack`) before Docker startup
  - Dependencies: Tasks 1.2, 1.6

---

### Milestone 2: Shared backend version metadata API

**Description:** Add backend endpoint that reports installed and latest versions for backend/CLI/Studio with cached registry lookups.

#### Task Checklist

- [x] Task 2.1: Implement `/api/system/info` response model and router wiring
  - Dependencies: Milestone 1
- [x] Task 2.2: Add registry clients for PyPI (`orcheo-sdk`, `orcheo-backend`) and npm (`orcheo-studio`)
  - Dependencies: Task 2.1
- [x] Task 2.3: Add in-memory caching with configurable TTL and timeout/retry policy
  - Dependencies: Task 2.2
- [x] Task 2.4: Add API tests for success and registry-failure fallback
  - Dependencies: Task 2.1

---

### Milestone 3: Studio version display and reminders

**Description:** Surface Studio and backend versions in UI and remind users when updates are available.

#### Task Checklist

- [x] Task 3.1: Add Studio version source to frontend build/runtime metadata
  - Dependencies: Milestone 2
- [x] Task 3.2: Build version status UI component in a persistent location (top nav/settings)
  - Dependencies: Task 3.1
- [x] Task 3.3: Add reminder banner/toast when updates are available
  - Dependencies: Task 3.2
- [x] Task 3.4: Add 24h browser cache gate for update checks
  - Dependencies: Task 3.2
- [x] Task 3.5: Add component/integration tests for render and reminder behavior
  - Dependencies: Task 3.3

---

### Milestone 4: CLI 24h update reminder flow

**Description:** Check for CLI/backend updates on first CLI run within a 24-hour window and show non-blocking reminders.

#### Task Checklist

- [x] Task 4.1: Implement startup update-check hook in CLI main callback
  - Dependencies: Milestone 2
- [x] Task 4.2: Add 24h cache keying by profile + API URL
  - Dependencies: Task 4.1
- [x] Task 4.3: Compare installed/local versions to latest metadata and format concise reminders
  - Dependencies: Task 4.1
- [x] Task 4.4: Suppress reminders for non-stable/dev/private local versions
  - Dependencies: Task 4.1
- [x] Task 4.5: Add opt-out controls (`ORCHEO_DISABLE_UPDATE_CHECK`, no-check flag)
  - Dependencies: Task 4.1
- [x] Task 4.6: Add CLI tests for once-per-24h gating, non-stable suppression, and soft-fail behavior
  - Dependencies: Tasks 4.2, 4.4

---

### Milestone 5: Documentation, release, and validation

**Description:** Update docs and validate end-to-end behavior across setup, Studio, and CLI.

#### Task Checklist

- [x] Task 5.1: Update docs landing page with newest shortest installation path
  - Dependencies: Milestones 1, 4
- [x] Task 5.2: Update `README.md` with new single-command install/upgrade flow
  - Dependencies: Milestones 1, 4
- [x] Task 5.3: Update `homepage/` with newest shortest installation path
  - Dependencies: Milestones 1, 4
- [x] Task 5.4: Update manual setup docs with new single-command path and upgrade path
  - Dependencies: Milestones 1, 4
- [x] Task 5.4a: Update `docs/environment_variables.md` with new setup/update and reminder variables (`ORCHEO_DISABLE_UPDATE_CHECK`, `ORCHEO_UPDATE_CHECK_TTL_HOURS`, plus any setup/auth env flags)
  - Dependencies: Milestones 1, 2, 4
- [x] Task 5.5: Update Studio and CLI docs/screenshots for version/reminder UX
  - Dependencies: Milestones 3, 4
- [x] Task 5.5a: Align setup docs and env-var docs with canonical stack asset source (`deploy/stack`) and override knobs
  - Dependencies: Milestone 1
- [ ] Task 5.6: Run end-to-end QA matrix (fresh install, upgrade, stale version reminders)
  - Dependencies: Milestones 1, 3, 4
- [ ] Task 5.7: Roll out and monitor support issues related to install/update flows
  - Dependencies: Task 5.6

---

### Milestone 6 (P1): Windows bootstrap parity

**Description:** Deliver full PowerShell bootstrap support after Unix bootstrap is stable.

#### Task Checklist

- [x] Task 6.1: Implement PowerShell bootstrap equivalent with `uv` detection/install and `uvx orcheo-sdk install` handoff
  - Dependencies: Milestone 1
- [ ] Task 6.2: Validate Windows paths, shell behavior, and non-interactive setup scenarios
  - Dependencies: Task 6.1
- [x] Task 6.3: Publish Windows-specific setup/upgrade docs and troubleshooting notes
  - Dependencies: Task 6.2

---

### Milestone 7 (P1): Compatibility and recovery UX

**Description:** Improve update guidance with compatibility metadata, release context, and rollback instructions.

#### Task Checklist

- [x] Task 7.1: Add compatibility matrix metadata support in backend version API and consumers
  - Dependencies: Milestone 2
- [x] Task 7.2: Distinguish "update available" from "recommended minimum" in Studio and CLI messaging
  - Dependencies: Task 7.1
- [x] Task 7.3: Add optional "what changed" links to reminder output when release notes are available
  - Dependencies: Milestones 3, 4
- [x] Task 7.4: Add rollback/recovery guidance for partial or failed upgrades in setup summaries and docs
  - Dependencies: Milestones 1, 5

---

## Validation Gates

- Python changes: `make format`, `make lint`, and targeted `uv run pytest ...` for installer/backend/CLI tests.
- Studio changes: `make studio-format`, `make studio-lint`, and targeted Studio tests.
- Release readiness: verify reminders appear once per 24h and never block command execution.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-21 | Codex | Initial draft |
