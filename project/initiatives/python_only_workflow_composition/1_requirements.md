# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Python-Only Workflow Composition
- **Type:** Enhancement
- **Summary:** Standardize workflow composition on Python LangGraph ingestion, remove JSON workflow composition paths and the Orcheo MCP SDK server (`packages/sdk/` MCP module), and move removed code to `legacy/` as non-runtime reference.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-07

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Prior Artifacts | `project/initiatives/workflow_upload_config/1_requirements.md` | Shaojie Jiang | Workflow upload runnable config requirements |
| Design Review | `project/initiatives/workflow_upload_config/2_design.md` | Shaojie Jiang | Workflow upload runnable config design |
| Eng Requirement Doc | `project/initiatives/python_only_workflow_composition/2_design.md` | Shaojie Jiang | Python-only workflow composition design |
| Project Plan | `project/initiatives/python_only_workflow_composition/3_plan.md` | Shaojie Jiang | Python-only workflow composition plan |

## PROBLEM DEFINITION
### Objectives
Unify workflow authoring and execution around Python LangGraph scripts so there is one supported composition path across backend and tooling. Remove JSON composition and MCP workflow surfaces that now create maintenance overhead and split behavior.

### Target users
- Platform/backend engineers maintaining workflow execution paths
- CLI users uploading and operating workflows
- Studio users configuring workflows (config-only in this phase)

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform engineer | Keep only Python ingestion (`/versions/ingest`) as version-creation path | Runtime and ingestion behavior are consistent | P0 | Direct JSON version creation path is removed; Python ingest remains functional |
| CLI user | Upload and download workflows in Python format only | Tooling behavior matches platform support | P0 | `.json` upload/download paths are removed with clear error guidance |
| CLI user | Update runnable config on an existing workflow version without creating a new version | Runtime defaults can be tuned safely after Python ingestion | P0 | CLI config-save flow calls version runnable-config update API and does not call version-create endpoints |
| Studio user | Save runnable configuration without composing graph JSON versions | Studio remains usable for config management while composition standardizes on Python | P0 | Studio no longer creates workflow versions from graph JSON; config writes persist to backend version runnable config |
| Platform operator | Remove the Orcheo MCP SDK server (`packages/sdk/` MCP module) | Eliminate the SDK-hosted MCP composition surface | P0 | `orcheo_sdk.mcp_server` module is removed from active runtime |
| Engineering team | Keep removed code for reference only | We preserve historical context without runtime complexity | P0 | Removed implementation moved under `legacy/` and excluded from runtime/lint/tests |

### Context, Problems, Opportunities
The codebase currently supports two workflow composition paradigms: direct JSON graph composition and Python LangGraph script ingestion. This duplicates ingestion/build/runtime logic, complicates API/tooling contracts, and increases the chance of behavior drift. The Orcheo MCP SDK server (`packages/sdk/src/orcheo_sdk/mcp_server/`) exposes workflow composition tools (node, edge, workflow, codegen tools) to external AI agents; this surface is now superseded by Agent Skills and adds maintenance overhead.

The opportunity is to reduce execution risk and maintenance burden by enforcing one canonical composition format (Python LangGraph). The tradeoff is intentional: loss of non-executing data-only workflow representation and reduced language-agnostic composition APIs. Existing JSON workflow versions are accepted as a hard break in this initiative.

### Product goals and Non-goals
Goals:
- Make Python LangGraph the only supported workflow composition path.
- Remove JSON composition paths and the Orcheo MCP SDK server from active runtime/tooling.
- Preserve removed code in `legacy/` for future reference.

Non-goals:
- Backward compatibility for existing JSON workflow versions.
- Automatic migration from JSON versions to Python versions.

## PRODUCT DEFINITION
### Requirements
- **P0: Python-only workflow version creation and execution**
  - Remove direct JSON graph version creation API path.
  - Keep `/api/workflows/{workflow_ref}/versions/ingest` as the only workflow version creation path.
  - Runtime graph build must only support `langgraph-script` format.
  - Existing non-script versions fail with explicit unsupported-format errors.

- **P0: CLI/SDK workflow format reduction**
  - `workflow upload` supports only `.py` input.
  - `workflow download` supports Python output only.
  - Remove JSON-specific branches from workflow upload/download services and format handlers.
  - Add CLI/SDK support for config-only saves by updating existing version `runnable_config` via backend update endpoint (no version creation).

- **P0: Studio behavior change (config-only)**
  - Studio Save no longer creates backend workflow versions from graph (nodes/edges) edits.
  - Studio writes runnable configuration to backend using version-level `runnable_config`.
  - If no Python version exists, Studio returns a clear blocking error.

- **P0: Orcheo MCP SDK server removal**
  - Remove the `orcheo_sdk.mcp_server` module (`packages/sdk/src/orcheo_sdk/mcp_server/`) and its workflow, node, edge, codegen, credential, service-token, and agent-tool MCP bindings from active runtime.

- **P0: Legacy archival policy**
  - Move removed code/tests/docs to `legacy/`.
  - `legacy/` is archive-only: excluded from package/runtime imports, lint, and test discovery.

- **P1: Documentation alignment**
  - Update CLI and developer docs to state Python-only workflow composition and removed MCP workflow tools.

### Designs (if applicable)
See `project/initiatives/python_only_workflow_composition/2_design.md` for end-to-end API, runtime, and tooling design.

### Other Teams Impacted
- **Studio Frontend:** Save semantics shift from versioning to config-only writes.
- **SDK/CLI Maintainers:** JSON format options are removed from commands/services, and config-save flow is updated to version `runnable_config` updates.
- **Developer Experience:** Orcheo MCP SDK server is removed; users composing workflows via MCP are directed to Agent Skills.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
This initiative collapses composition around script ingestion and script-backed execution. Backend, CLI/SDK, and Studio all align to Python LangGraph as the only workflow representation for version creation.

### Technical Requirements
- Remove JSON graph creation and build paths from backend workflow APIs and runtime graph builder.
- Add/keep a backend path for Studio config-only writes targeting `WorkflowVersion.runnable_config`.
- Update CLI/SDK config-save path to use backend version `runnable_config` update endpoint (config-only, no new version).
- Preserve existing runnable config precedence where per-run config overrides stored version config.
- Remove the Orcheo MCP SDK server module (`orcheo_sdk.mcp_server`) from active runtime exports.
- Move removed implementation to `legacy/` and ensure tooling excludes it.

## MARKET DEFINITION
Internal platform enhancement; no external market analysis required.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Python-only ingestion adoption | 100% of new workflow versions created via `/versions/ingest` |
| [Secondary] JSON composition path elimination | 0 active runtime calls to removed JSON version-creation endpoints |
| [Guardrail] Runtime reliability | No regression in Python workflow execution success rates after rollout |

### Rollout Strategy
Ship in three sequential phases: backend/runtime consolidation first, then Studio and CLI/SDK alignment, then MCP SDK server removal and legacy archival. Merge behind short-lived flags only if needed for deployment sequencing within a phase; feature intent is progressive consolidation.

### Estimated Launch Phases (if applicable)

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal engineering environments | Remove JSON version-creation and runtime paths from backend; add explicit unsupported-format errors for legacy graph payloads |
| **Phase 2** | Internal engineering environments | Align Studio (config-only save) and CLI/SDK (remove JSON upload/download, add config-only save) with Python-only composition |
| **Phase 3** | All environments | Remove Orcheo MCP SDK server module, archive removed code under `legacy/`, publish updated docs and migration guidance |

## HYPOTHESIS & RISKS
Hypothesis: enforcing one composition format (Python LangGraph) will reduce maintenance cost and runtime ambiguity while preserving core workflow capabilities for supported paths.

Risks:
- Hard break on existing JSON versions may block affected workflows.
- Studio users may perceive reduced functionality when graph version save is removed.
- Removing the Orcheo MCP SDK server may impact untracked internal automation that relies on the SDK MCP composition tools.

Risk mitigation:
- Emit explicit unsupported-format errors with actionable remediation (re-upload via Python ingestion).
- Update Studio UX messaging to clearly indicate config-only behavior.
- Document Agent Skills replacement path and update internal runbooks.

## APPENDIX
- Existing JSON workflow compatibility is intentionally out of scope for this initiative.
