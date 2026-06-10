# Design Document

## For Python-Only Workflow Composition

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-07
- **Status:** Approved

---

## Overview

This initiative makes Python LangGraph script ingestion the sole supported workflow composition path. It removes JSON workflow composition from active backend APIs/runtime and from CLI/SDK workflow upload/download format handling, and removes the Orcheo MCP SDK server (`packages/sdk/src/orcheo_sdk/mcp_server/`) now replaced by Agent Skills.

Studio behavior is narrowed to config-only persistence in this phase: Studio does not create workflow versions and instead updates version-level `runnable_config` on existing Python workflow versions.

The compatibility stance is explicit hard break for pre-existing JSON graph versions. These versions remain in storage but are not executable after this refactor; runtime emits deterministic unsupported-format errors.

## Components

- **Backend Workflow Routers (FastAPI, Backend Team)**
  - Keep script ingestion endpoint and remove JSON version-creation endpoint from active surface.
  - Expose config-only update path for version `runnable_config`.

- **Runtime Graph Builder/Execution (Core Runtime Team)**
  - Support only `langgraph-script` graph payloads for build/execute.
  - Return explicit errors for legacy non-script graph payloads.

- **Studio Workflow Storage Layer (Studio Team)**
  - Stop posting graph versions.
  - Persist only runnable config updates against existing versions.

- **CLI/SDK Workflow Services (SDK Team)**
  - Remove JSON upload and JSON download behavior.
  - Keep Python ingestion and Python export flows.
  - Add config-only save flow that updates version `runnable_config` without creating a version.

- **Orcheo MCP SDK Server (SDK Team)**
  - Remove the `orcheo_sdk.mcp_server` module and its tool registrations/wrappers from active runtime.
  - This covers workflow, node, edge, codegen, credential, service-token, and agent-tool MCP bindings hosted in `packages/sdk/`.

- **Legacy Archive (`legacy/`) (Platform Team)**
  - Store removed code/tests/docs for reference only.
  - Excluded from runtime packaging, lint, and tests.

## Request Flows

### Flow 1: Python Workflow Upload (CLI/SDK)

1. User runs workflow upload with a Python file.
2. CLI/SDK reads script and posts to `/api/workflows/{workflow_ref}/versions/ingest`.
3. Backend ingests script, summarizes graph, stores version payload (`format=langgraph-script`) and optional `runnable_config`.
4. Workflow execution uses stored script-backed version.

### Flow 2: Studio Config-Only Save

1. User edits runnable config in Studio.
2. Studio calls runnable-config update endpoint for selected/latest version.
3. Backend validates and persists config into `WorkflowVersion.runnable_config`.
4. No workflow graph version is created from Studio.

### Flow 2b: CLI Config-Only Save

1. User updates runnable config for an existing workflow version via CLI.
2. CLI resolves target workflow/version and calls runnable-config update endpoint.
3. Backend validates and persists config into `WorkflowVersion.runnable_config`.
4. No workflow graph version is created from CLI config save.

### Flow 3: Workflow Execution

1. Run is created with optional per-run runnable config.
2. Runtime resolves stored `version.runnable_config`.
3. Runtime merges configs with current precedence (run overrides version).
4. Graph is built only if version graph is `langgraph-script`; otherwise fail fast with unsupported-format error.

## API Contracts

Existing contract (already implemented):
```text
POST /api/workflows/{workflow_ref}/versions/ingest
Body:
  script: string
  entrypoint: string | null
  metadata: object
  runnable_config: object | null
  notes: string | null
  created_by: string

Response:
  201 Created -> WorkflowVersion
  400 -> Script ingestion/validation error
  404 -> Workflow not found
```

New contract (to be added in this initiative; consumed by Studio and CLI config-save flows):
```text
PUT /api/workflows/{workflow_ref}/versions/{version_number}/runnable-config
Body:
  runnable_config: object | null
  actor: string

Response:
  200 OK -> WorkflowVersion (updated runnable_config)
  404 -> Workflow/version not found
  422 -> Config validation error
```

Existing contract (planned removal in this initiative):
```text
Removed endpoint:
POST /api/workflows/{workflow_ref}/versions
(legacy JSON graph version creation path)
```

## Data Models / Schemas

| Field | Type | Description |
|-------|------|-------------|
| `WorkflowVersion.graph.format` | string | Must be `langgraph-script` for executable versions |
| `WorkflowVersion.graph.source` | string | Stored Python script |
| `WorkflowVersion.graph.summary` | object | Serialized graph summary for inspection/visualization |
| `WorkflowVersion.runnable_config` | object \| null | Stored default runtime config for that version |

## Security Considerations

- Continue existing auth/authz guards on workflow/version endpoints.
- Validate `runnable_config` through existing runnable config schema model.
- Keep script ingestion sandbox and size/timeout checks unchanged.
- Ensure legacy non-script graph errors do not leak sensitive payload details.

## Performance Considerations

- Simplifying to one composition path reduces conditional branches at runtime.
- Config-only Studio saves avoid graph compilation/serialization work.
- No additional storage model introduced; reuse existing version payloads.

## Testing Strategy

- **Unit tests**
  - Graph builder rejects non-script graph payloads.
  - CLI upload/download reject removed JSON format paths.
  - CLI config-only save calls runnable-config update endpoint and does not create a version.
  - Config-update endpoint validates and persists runnable config correctly.

- **Integration tests**
  - `/versions/ingest` path remains functional end-to-end.
  - Studio config-only save updates version runnable config without creating new version.
  - CLI config-only save updates version runnable config without creating new version.
  - Runtime fails cleanly for legacy JSON versions and runs normally for script versions.

- **Manual QA checklist**
  - Upload `.py` workflow and execute successfully.
  - Attempt `.json` upload/download and verify clear errors.
  - Save runnable config from Studio and confirm persistence in version details.
  - Save runnable config from CLI and confirm version count is unchanged while `runnable_config` is updated.
  - Verify Orcheo MCP SDK server tools are no longer exposed.

## Rollout Plan

1. Phase 1: Backend/runtime + CLI/SDK refactor with explicit unsupported-format errors.
2. Phase 2: Studio + CLI config-only save behavior release.
3. Phase 3: Remove Orcheo MCP SDK server module and finalize docs.

Include release notes calling out hard break for existing JSON graph versions and migration path (re-ingest from Python script).

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-07 | Codex | Initial draft |
