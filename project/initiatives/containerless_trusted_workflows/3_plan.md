# Project Plan

## For Containerless Trusted Workflows

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-06-05
- **Status:** Completed

---

## Overview

Remove gVisor sandboxing and external CLI-agent support by changing production workflow ingestion and execution to trusted declarative graphs only. Self-hosted and developer modes may retain unsafe arbitrary-code behavior explicitly, but production must reject arbitrary-code formats at both ingestion and execution.

**Related Documents:**
- Requirements: `project/initiatives/containerless_trusted_workflows/1_requirements.md`
- Design: `project/initiatives/containerless_trusted_workflows/2_design.md`

---

## Milestones

### Milestone 1: Remove External CLI-Agent Support

**Description:** Delete active runtime support for Claude Code, Codex, Gemini, and generic external-agent workflow nodes. Success criterion: no production node registry entries, worker tasks, API routes, Studio flows, docs, or tests require CLI-agent providers.

#### Task Checklist

- [x] Task 1.1: Remove `ExternalAgentNode`, `ClaudeCodeNode`, `CodexNode`, `GeminiNode`, and duplicate legacy/AI external-agent modules from active node exports and registry registration
  - Dependencies: None
- [x] Task 1.2: Remove external-agent runtime manager, provider models, auth path helpers, and process-launch integration
  - Dependencies: Task 1.1
- [x] Task 1.3: Remove worker login/status/refresh tasks and backend/Studio API surfaces for external-agent auth
  - Dependencies: Task 1.2
- [x] Task 1.4: Audit workflow remediation/autofix and remove or replace CLI-agent-dependent behavior
  - Dependencies: Task 1.2
- [x] Task 1.5: Update templates, examples, docs, and tests that reference CLI-agent workflow nodes
  - Dependencies: Task 1.1

---

### Milestone 2: Define Declarative Trusted Workflow Model

**Description:** Introduce the production-safe workflow representation and policy layer. Success criterion: production can validate a declarative workflow without executing Python or importing tenant code.

#### Task Checklist

- [x] Task 2.1: Define declarative workflow graph schema (`format`, `version`, `nodes`, `edges`, `conditional_edges`, `triggers`, `listeners`, `credential_references`, `metadata`)
  - Dependencies: None
- [x] Task 2.2: Define workflow trust mode config with `production`, `self_host_unsafe`, and `developer`
  - Dependencies: None
- [x] Task 2.3: Implement trusted node policy allowlist and rejection reasons
  - Dependencies: Task 2.1, Task 2.2
- [x] Task 2.4: Add ingestion-time policy enforcement for production declarative payloads
  - Dependencies: Task 2.3
- [x] Task 2.5: Add execution-time policy enforcement before graph build/run
  - Dependencies: Task 2.3
- [x] Task 2.6: Add tests for unknown nodes, plugin nodes, raw callables, lambdas, code nodes, JS nodes, external-agent nodes, and non-serializable configs
  - Dependencies: Task 2.4, Task 2.5

---

### Milestone 3: Replace Python Execution During Ingestion

**Description:** Stop using Python script execution to derive workflow metadata in production. Success criterion: production ingestion never calls `compile`, `eval`, imports workflow modules, or calls workflow entrypoints.

#### Task Checklist

- [x] Task 3.1: Replace script-backed ingestion response generation with declarative graph summary generation
  - Dependencies: Milestone 2
- [x] Task 3.2: Derive node summary, edges, conditional edges, cron index, and listener index directly from declarative graph payloads
  - Dependencies: Task 3.1
- [x] Task 3.3: Reject Python-source ingestion in production mode with explicit errors
  - Dependencies: Task 2.2
- [x] Task 3.4: Restrict existing Python loader to `self_host_unsafe` and `developer` modes if retained
  - Dependencies: Task 3.3
- [x] Task 3.5: Add optional AST scanning warnings/errors for unsafe modes without using it as the production security boundary
  - Dependencies: Task 3.4

---

### Milestone 4: Declarative Execution Path

**Description:** Build and run workflows from declarative graphs in the worker process. Success criterion: trusted declarative workflows execute without sandbox runtime configuration.

#### Task Checklist

- [x] Task 4.1: Implement declarative graph builder that constructs `StateGraph` from trusted registry node constructors and JSON config
  - Dependencies: Milestone 2
- [x] Task 4.2: Update worker execution to use declarative graph builder for production graph payloads
  - Dependencies: Task 4.1
- [x] Task 4.3: Replace sandbox credential broker usage with normal vault credential resolution in the production execution path
  - Dependencies: Task 4.2
- [x] Task 4.4: Remove sandbox dispatcher/launcher use from trusted production workflow execution
  - Dependencies: Task 4.2
- [x] Task 4.5: Add integration tests proving trusted declarative workflows run with no sandbox env vars configured
  - Dependencies: Task 4.4

---

### Milestone 5: Mermaid and Candidate Catalog Workarounds

**Description:** Move preview generation to safe data-only paths. Success criterion: Mermaid and candidate previews no longer require executing workflow Python.

#### Task Checklist

- [x] Task 5.1: Add backend/API path or serializer support to render Mermaid from declarative graph summaries on demand
  - Dependencies: Milestone 2
- [x] Task 5.2: Update Studio/SDK consumers to treat Mermaid as optional derived data
  - Dependencies: Task 5.1
- [x] Task 5.3: Define candidate manifest format for graph metadata in `config.json`, frontmatter, or a dedicated manifest file
  - Dependencies: Milestone 2
- [x] Task 5.4: Update candidate catalog parser to read manifests and stop calling sandboxed/script ingestion for preview enrichment
  - Dependencies: Task 5.3
- [x] Task 5.5: Update candidate UX to omit previews or mark install unavailable when a candidate has no declarative manifest
  - Dependencies: Task 5.4

---

### Milestone 6: Remove Sandbox Runtime Infrastructure

**Description:** Delete the gVisor/container sandbox subsystem after production no longer needs arbitrary-code isolation. Success criterion: production stack has no sandbox-runtime service, workspace-sandbox image, Docker socket mount, runsc dependency, or sandbox-only network/credential components.

#### Task Checklist

- [x] Task 6.1: Remove `src/orcheo/sandbox/` modules that are only used for container dispatch, remote exec, workflow runner, ingestion runner, egress, metrics, and broker tokens
  - Dependencies: Milestone 4, Milestone 5
- [x] Task 6.2: Remove sandbox-runtime service and credential-relay-only sandbox endpoints from backend wiring
  - Dependencies: Task 6.1
- [x] Task 6.3: Remove `Dockerfile.workspace-sandbox`, `Dockerfile.sandbox-runtime`, sandbox build Makefile targets, runsc/gVisor config, and sandbox compose services/networks
  - Dependencies: Task 6.2
- [x] Task 6.4: Remove sandbox environment variables from docs and stack templates
  - Dependencies: Task 6.3
- [x] Task 6.5: Delete or rewrite sandbox-specific tests
  - Dependencies: Task 6.1

---

### Milestone 7: Production Hardening and Documentation

**Description:** Make the new trust model explicit and difficult to misconfigure in production. Success criterion: production rejects unsafe workflows by default and operator/developer docs explain the tradeoffs.

#### Task Checklist

- [x] Task 7.1: Ensure production stack templates set/enforce `production` workflow trust mode
  - Dependencies: Milestone 2
- [x] Task 7.2: Add startup checks that reject production deployments configured with unsafe arbitrary-code mode
  - Dependencies: Task 7.1
- [x] Task 7.3: Add audit logs/metrics for policy rejections at ingestion and execution
  - Dependencies: Task 2.4, Task 2.5
- [x] Task 7.4: Update developer, operator, SDK, candidate authoring, and security docs
  - Dependencies: Milestone 6
- [x] Task 7.5: Run full backend/SDK/Studio test suites and remove stale fixtures
  - Dependencies: Milestone 6, Task 7.4

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-05 | Codex | Initial draft |
| 2026-06-05 | Codex | Milestones 1-2 completed |
| 2026-06-05 | Codex | Milestones 3-7 completed — all tasks done |
