# Design Document

## For Canvas Workflow Visualisation

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-02-20
- **Status:** Approved

---

## Overview

This design replaces the current `Editor` and `Execution` tabs in Canvas with a single `Workflow` tab centered on Mermaid diagram visualization for LangGraph workflows. Existing Editor/Execution implementations will be moved into the confirmed legacy namespace so the code is preserved for rollback/reference but detached from the active UI path.

The Workflow tab adds a config entry point that opens workflow-level configuration using the same form stack currently used by node configuration (`@rjsf/core` + existing custom templates/widgets/validator). Workflow config persistence happens only on explicit config save. This avoids introducing a parallel form system and keeps consistency in validation and rendering behavior.

The Workflow config sheet must stay contract-compatible with CLI upload runnable config input so users see the same payload they set with `orcheo workflow upload --config` or `--config-file`.

The gallery interaction model is updated so workflow cards are directly clickable to open workflows. Action controls (menu, favorite, edit button, template actions) preserve current behavior via event-handling boundaries.

## Components

- **Workflow Tabs Surface (Canvas Frontend)**
  - Update tab triggers and tab content wiring to remove `canvas`/`execution` from active navigation and add `workflow`.
  - Files:
    - `apps/canvas/src/features/workflow/components/panels/workflow-tabs.tsx`
    - `apps/canvas/src/features/workflow/pages/workflow-canvas/components/workflow-canvas-layout.tsx`
    - `apps/canvas/src/features/workflow/pages/workflow-canvas/hooks/use-canvas-ui-state.ts`

- **Workflow Mermaid Panel (Canvas Frontend)**
  - New panel component for Mermaid rendering and status states.
  - Proposed files:
    - `apps/canvas/src/features/workflow/pages/workflow-canvas/components/workflow-tab-content.tsx`
  - Mermaid definition is read from `ApiWorkflowVersion.mermaid`.

- **Workflow Version Mermaid Serialization (Backend)**
  - Reuse Mermaid generation behavior aligned with CLI workflow show output and expose it in workflow version responses.
  - Proposed files:
    - `apps/backend/src/orcheo_backend/app/routers/workflows.py`
    - `apps/backend/src/orcheo_backend/app/schemas/workflows.py` (if dedicated response schema is introduced)
    - `packages/sdk/src/orcheo_sdk/cli/workflow/mermaid.py` (reuse/extract logic to shared utility as needed)

- **Workflow Config Form (Canvas Frontend)**
  - Extract/reuse shared schema form wrapper from node inspector config stack.
  - Proposed files:
    - `apps/canvas/src/features/workflow/components/forms/schema-config-form.tsx`
    - `apps/canvas/src/features/workflow/pages/workflow-canvas/components/workflow-config-sheet.tsx`
  - Existing dependencies reused:
    - `apps/canvas/src/features/workflow/components/panels/rjsf-theme.tsx`
    - `apps/canvas/src/features/workflow/components/panels/node-inspector/config-panel.tsx`

- **Legacy Editor/Execution Namespace**
  - Move removed-tab code paths under `legacy` folder.
  - Proposed structure:
    - `apps/canvas/src/features/workflow/legacy/workflow-canvas/components/canvas-tab-content.tsx`
    - `apps/canvas/src/features/workflow/legacy/workflow-canvas/components/execution-tab-content.tsx`
    - `apps/canvas/src/features/workflow/legacy/workflow-canvas/components/workflow-canvas-layout.legacy.tsx` (optional snapshot)
  - No active imports from new tab flow.

- **Gallery Click Target Update**
  - Make card container open workflow on click, preserving control-level interactions.
  - File:
    - `apps/canvas/src/features/workflow/pages/workflow-gallery/workflow-card.tsx`

## Request Flows

### Flow 1: Open Workflow and View Mermaid Diagram

1. User opens a workflow from gallery.
2. Workflow page defaults to `Workflow` tab.
3. Workflow tab reads latest workflow version payload including `mermaid`.
4. Mermaid definition is rendered directly from API response.
5. If graph is unavailable/invalid, tab shows non-blocking empty/error state.

### Flow 2: Open Workflow Config from Workflow Tab

1. User clicks `Config` button in Workflow tab header.
2. Config sheet/dialog opens.
3. Form renders using shared schema form wrapper and existing RJSF theme assets.
4. User edits workflow-level config and saves.
5. Config persists only when user explicitly clicks save in the config sheet/dialog and is stored as `runnable_config`.
6. Supported fields mirror upload-time runnable config contract: `configurable`, `tags`, `metadata`, `callbacks`, `run_name`, `recursion_limit`, `max_concurrency`, `prompts`.

### Flow 3: Gallery Card Click Navigation

1. User clicks card body area.
2. Card click handler routes to `/workflow-canvas/{workflowId}`.
3. If click originated from menu/button controls, propagation is stopped and no card navigation occurs.

## API Contracts

Canvas consumes Mermaid from the existing workflow versions endpoint:

```http
GET /api/workflows/{workflow_id}/versions
Response: WorkflowVersion[]
```

Canvas type updates required:

```ts
interface ApiWorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  graph: Record<string, unknown>;
  mermaid?: string | null;
  metadata: unknown;
  runnable_config?: Record<string, unknown> | null;
  notes: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}
```

`mermaid` is generated server-side using the same logic family already used by CLI workflow show output so Canvas and CLI stay consistent.

## Data Models / Schemas

Workflow config form schema (initial scope aligned with runnable config model):

| Field | Type | Description |
|-------|------|-------------|
| configurable | object | Runnable `configurable` payload |
| run_name | string | Optional run display name |
| tags | string[] | Workflow run tags |
| metadata | object | Arbitrary JSON metadata |
| callbacks | array | JSON-serialisable callbacks payload |
| recursion_limit | number | Max recursion depth |
| max_concurrency | number | Execution concurrency cap |
| prompts | object | Named trainable prompts keyed by prompt name |

Workflow version API additions:

| Field | Type | Description |
|-------|------|-------------|
| mermaid | string \| null | Server-generated Mermaid diagram for the version graph |
| runnable_config | object \| null | Persisted workflow-level runnable config, saved via explicit config save |

Draft shape in Canvas state:

```json
{
  "runnable_config": {
    "configurable": {
      "workspace": "team-a"
    },
    "run_name": "workflow-v2",
    "tags": ["canvas", "prod"],
    "metadata": {
      "owner": "team-a"
    },
    "callbacks": [],
    "recursion_limit": 25,
    "max_concurrency": 4,
    "prompts": {
      "summary_prompt": {
        "template": "Summarize {topic}",
        "input_variables": ["topic"]
      }
    }
  }
}
```

## Security Considerations

- Preserve existing auth behavior for workflow version loading.
- Treat `runnable_config.metadata` as user-provided content; do not execute/interpret as code.
- Treat `mermaid` as text data and render through trusted Mermaid renderer configuration.

## Performance Considerations

- Mermaid rendering should be memoized by `{workflowId, versionId, mermaid hash}`.
- Large graph rendering should avoid blocking main thread where possible (defer render until tab active).
- Card click behavior adds no significant cost.

## Testing Strategy

- **Unit tests**
  - Backend Mermaid serialization from version graph payloads (including invalid/edge graph cases).
  - Shared schema config form wrapper rendering with custom widgets/templates.
  - Card click propagation behavior.
- **Component tests**
  - Workflow tabs render includes `Workflow` and excludes `Editor`/`Execution`.
  - Workflow config button opens and saves form data.
- **Integration tests**
  - `workflow-canvas` route defaults to Workflow tab and renders Mermaid state.
  - Gallery card click opens correct workflow route.
- **Regression tests**
  - Existing trace/readiness/settings tabs remain accessible.
  - Existing card menu actions still work without unintended navigation.

## Rollout Plan

1. Land tab migration + legacy move with tests.
2. Land backend Mermaid payload support for workflow versions.
3. Land Workflow Mermaid tab and config form reuse with tests.
4. Land gallery card click enhancements.
5. Validate end-to-end manually in Canvas before release.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-20 | Codex | Initial draft |
