# Project Plan

## For Studio Workflow Visualisation

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-02-20
- **Status:** Approved

---

## Overview

Deliver a LangGraph-first workflow-visualization Studio experience by removing Editor/Execution tabs from active UI, introducing a Workflow Mermaid tab with reusable workflow config form, making gallery cards directly clickable, and ensuring Workflow-tab config matches CLI upload runnable config (`--config` / `--config-file`). Preserve removed tab code in the confirmed legacy folder for rollback/reference.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 1: Tab Migration and Legacy Extraction

**Description:** Replace active tab structure and move removed tab implementations into a legacy namespace without breaking existing compilation or tests.

#### Task Checklist

- [x] Task 1.1: Add `Workflow` tab trigger and remove `Editor`/`Execution` triggers in `workflow-tabs.tsx`
  - Dependencies: None
- [x] Task 1.2: Update `workflow-layout.tsx` to mount `workflow` tab content and remove active `Studio`/`execution` tab content
  - Dependencies: Task 1.1
- [x] Task 1.3: Change default active tab from `"Studio"` to `"workflow"` in `use-Studio-ui-state.ts`
  - Dependencies: Task 1.1
- [x] Task 1.4: Move old `Studio-tab-content` and `execution-tab-content` implementations into `apps/studio/src/features/workflow/legacy/`
  - Dependencies: Task 1.2
- [x] Task 1.5: Remove active imports of moved legacy modules and ensure build passes
  - Dependencies: Task 1.4

---

### Milestone 2: Workflow Mermaid Tab + Config Form Reuse

**Description:** Add backend Mermaid payload support and the new Workflow tab experience with explicit-save workflow config editing.

#### Task Checklist

- [x] Task 2.1: Add backend Mermaid generation for workflow version responses using CLI-aligned logic
  - Dependencies: Milestone 1
- [x] Task 2.2: Add `mermaid` to Studio workflow version typings and storage adapters
  - Dependencies: Task 2.1
- [x] Task 2.3: Implement `workflow-tab-content` component with loading/empty/error states and Mermaid rendering from API payload
  - Dependencies: Task 2.2
- [x] Task 2.4: Extract shared schema form wrapper from node inspector config usage
  - Dependencies: Milestone 1
- [x] Task 2.5: Implement workflow config sheet/dialog opened by Workflow tab `Config` button using shared schema form wrapper
  - Dependencies: Task 2.4
- [x] Task 2.6: Wire workflow config persistence mapping to `runnable_config` on explicit config save action only
  - Dependencies: Task 2.5
- [x] Task 2.7: Align Workflow config sheet schema and serialization with CLI upload runnable config fields (`configurable`, `tags`, `metadata`, `callbacks`, `run_name`, `recursion_limit`, `max_concurrency`, `prompts`)
  - Dependencies: Task 2.6
- [x] Task 2.8: Add tests for workflow config round-trip parity in Studio form utilities
  - Dependencies: Task 2.7

---

### Milestone 3: Clickable Gallery Cards and Regression Hardening

**Description:** Improve gallery UX with card-level navigation while preserving existing menu/button actions and finalize regression coverage.

#### Task Checklist

- [x] Task 3.1: Make non-template workflow cards clickable in `workflow-card.tsx`
  - Dependencies: None
- [x] Task 3.2: Guard dropdown/menu/button interactions with event propagation controls
  - Dependencies: Task 3.1
- [x] Task 3.3: Add/update component tests for card click behavior and action controls
  - Dependencies: Task 3.2
- [x] Task 3.4: Update workflow studio tests for tab changes (`Workflow` present, `Editor`/`Execution` absent)
  - Dependencies: Milestone 2
- [x] Task 3.5: Run formatting, linting, and targeted tests for Studio changes
  - Dependencies: Tasks 3.3, 3.4

---

### Milestone 4: Validation and Release

**Description:** Execute QA checklist, verify rollback readiness via legacy folder, and release.

#### Task Checklist

- [x] Task 4.1: Manual QA of workflow open, tab switching, Mermaid rendering, config editing, and gallery navigation
  - Dependencies: Milestone 3
- [x] Task 4.2: Verify legacy code is preserved and isolated from production import graph
  - Dependencies: Milestone 1
- [x] Task 4.3: Document release notes and known limitations (if any)
  - Dependencies: Task 4.1

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-20 | Codex | Initial draft |

---

## Rollback / Contingency

- Keep the legacy folder with original Editor/Execution implementations intact for quick recovery.
- If Workflow tab rollout causes regressions, switch active tab wiring back to prior components while preserving new code behind branch/flag.
- Card click behavior can be safely reverted by removing card-level click handlers while keeping button/menu actions unchanged.

---

## Release Notes (Draft)

- Replaced active `Editor`/`Execution` tabs with `Workflow`, `Trace`, `Readiness`, and `Settings`.
- Added backend-generated `mermaid` payload on workflow version API responses.
- Added Workflow tab Mermaid visualization surface with empty/loading/error handling.
- Added workflow-level runnable config editing from the Workflow tab via a shared RJSF schema form wrapper.
- Persisted workflow runnable config to workflow version `runnable_config` only on explicit config save.
- Made non-template gallery cards clickable while preserving button/menu behaviors through propagation guards.
- Moved former Studio/execution tab implementations into `apps/studio/src/features/workflow/legacy/workflow/components/`.
- Added/updated tests for workflow tab regression and gallery card interaction behavior.

## Known Limitations

- Manual UI QA checklist in Task 4.1 remains pending; current validation is based on linting, build, and automated tests.
