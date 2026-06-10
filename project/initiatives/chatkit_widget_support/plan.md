# Project Plan

## For ChatKit Widget Support for Orcheo Workflows

- **Version:** 0.1
- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2025-12-15
- **Status:** Approved

---

## Overview

Deliver end-to-end ChatKit widget support so Orcheo workflows can render widgets and handle widget actions in ChatKit UI surfaces (public page and Studio bubble). This plan tracks backend serialization/action handling, frontend wiring, and rollout. See related documents for requirements and design details.

Streaming widget updates are deferred until after this MVP completes.

**Related Documents:**
- Requirements: `./requirements.md`
- Design: `./design.md`

---

## Milestones

### Milestone 1: Backend widget serialization and action handling

**Description:** Teach the ChatKit server to emit widget items (via LangChain ToolMessages) and process widget actions into workflow invocations using ToolMessages as the widget source.

#### Task Checklist

- [x] Confirm widget payloads are available via ToolMessage artifact/content and hydrate them into `WidgetItem`s (ToolMessages are the widget source)
  - Dependencies: None
- [x] Confirm ChatKit history loads include the hydrated widget ToolMessages without additional storage work
  - Dependencies: None
- [x] Implement `action()` handler to route widget `Action` payloads to WorkflowExecutor
  - Dependencies: Widget serialization in place
- [x] Add validation/size caps and user-facing error notices for invalid widgets
  - Dependencies: None

---

### Milestone 2: Frontend wiring and UX validation

**Description:** Ensure ChatKit React surfaces render widgets and forward widget actions correctly.

#### Task Checklist

- [x] Add `widgets.onAction` handler and call `control.sendAction` in ChatKit React integration
  - Dependencies: Backend action endpoint available
- [x] Verify widget rendering in public page and Studio bubble (dev/staging)
  - Dependencies: Sample workflow with widgets
- [x] Handle error/notice display for widget failures without breaking chat
  - Dependencies: Backend error payloads

---

### Milestone 3: Testing, logging, and rollout

**Description:** Harden the feature with tests, logging, and staged deployment (no feature flag).

#### Task Checklist

- [x] Unit/integration tests covering widget serialization, action round-trip, and history load
  - Dependencies: Milestone 1
- [x] Add logging for widget render/action failures with thread/workflow ids
  - Dependencies: Backend hooks
  - Implemented structured logging in `apps/backend/src/orcheo_backend/app/chatkit/server.py` with thread/workflow/action context and covered by `tests/backend/test_chatkit_server_widgets.py`.
- [x] Stage rollout without feature flag; monitor error logs; promote to production
  - Dependencies: Milestones 1 and 2
  - Rollout path: keep widget support always-on, deploy to staging with sample widget workflow, monitor logs for `Skipping widget payload` and `Widget action failed` entries keyed by `thread_id`/`workflow_id`. Promote to production once staging is clean.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2025-12-15 | Shaojie Jiang | Initial draft |
