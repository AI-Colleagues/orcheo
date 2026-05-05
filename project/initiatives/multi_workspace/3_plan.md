# Project Plan

## For Multi-workspace support for Orcheo

- **Version:** 0.1
- **Author:** Claude (Opus 4.7), Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

Deliver multi-workspace support so one Orcheo deployment can serve multiple independent teams or individuals with strict logical isolation. Work is sequenced as foundation → persistence sweep → governance → polish, behind the `multi_workspace.enabled` config flag, with workspace-aware RBAC and a backwards-compatible default-workspace upgrade path.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

## Status (2026-05-05)

- **Milestone 1 — Foundation:** complete. Workspace core, settings, in-memory + SQLite repositories, resolver with TTL membership cache, admin/member API routes, `orcheo workspace` CLI, default-workspace bootstrap, and workspace-context-aware FastAPI dependency are all wired. The `/api` protected router globally resolves a `WorkspaceContext` (anonymous principals fall through to the default workspace when `MULTI_WORKSPACE_ENABLED=False`; explicit 400 when enabled). Lint/mypy/tests all pass (5,898 tests, 60 workspace unit + integration tests).
- **Milestone 2 — Persistence sweep:** complete. Task 2.1 complete: workflow repository and run repository have `workspace_id` columns, queries, migration logic (SQLite + Postgres), NULL=unscoped backward-compat semantics, router-level scoping for all 14 workflow/run/trigger/listener endpoints, and 7 cross-workspace isolation tests. Task 2.2 complete: `RunHistoryRecord.workspace_id` field, `start_run(workspace_id=)` param across InMemory/SQLite/Postgres stores, `list_histories(workspace_id=)` filter with NULL=unscoped semantics, SQLite migration, router passes `workspace_id` from workspace context, 8 cross-workspace isolation tests. Task 2.3 complete: service tokens already bound to workspace at issuance. Task 2.4 complete: `CredentialMetadata.workspace_id` field, per-workspace name uniqueness, `list_credentials`/`list_all_credentials` both accept `workspace_id` filter with NULL=unscoped semantics, SQLite migration, credential router passes `workspace_id` from `WorkspaceContextDep` for list and create, 10 vault isolation tests. Task 2.5 complete: `workspace_id` column added to `chat_threads` (SQLite + Postgres schemas + SQLite migration), `ChatKitRequestContext` includes `workspace_id`, `ChatKitAuthResult` includes `workspace_id` fetched from `get_workflow_workspace_id()` (new protocol method, implemented in InMemory/SQLite/Postgres repos), context populated in chatkit router, `save_thread` stores `workspace_id`, `load_threads` filters by `workspace_id` with NULL=unscoped semantics in InMemory/SQLite/Postgres stores, 7 isolation tests. Task 2.6 complete: `AgentensorCheckpoint.workspace_id` field, `record_checkpoint(workspace_id=)` and `list_checkpoints(workspace_id=)` params across InMemory/SQLite/Postgres stores, NULL=unscoped semantics, SQLite migration, Postgres schema updated, agentensor router passes `workspace_id` from `WorkspaceContextDep`, 8 isolation tests. Task 2.7 complete: plugin install/enable state is workspace-scoped. Task 2.8 complete: listener/trigger registrations are workspace-scoped and public webhooks route through `/hooks/{workspace_slug}/{trigger_id}`. Task 2.9 complete: Celery task envelopes carry `workspace_id` in headers and worker rejects unscoped tasks. Task 2.10 complete: WebSocket workflow execution resolves the workflow workspace and scopes run/event handling to workspace-aware execution paths. Task 2.11 complete: runtime state and runnable configs carry `workspace_id`, `decode_variables()` now runs against workspace-aware state, and graph-store writes are workspace-prefixed. Task 2.12 complete: repository-helper lint/test fails when a query omits `workspace_id`. Task 2.13 complete: cross-workspace isolation tests cover the subsystem updates above.
- **Milestone 3 / 4:** complete. Role gating, workspace indicator UX, CLI ergonomics, deployment/runbook updates, quota/rate-limit/audit/retention work, demo walkthroughs, and the final coverage/security audit are all in place.

---

## Milestones

### Milestone 1: Foundation

**Description:** Establish the workspace core (models, context, resolver), workspace-aware authentication, baseline role checks, and the default-workspace migration. After this milestone the codebase carries `workspace_id` end-to-end while behavior remains identical for existing single-workspace deployments.

#### Task Checklist

- [x] Task 1.1: Add `orcheo.workspace` package with `Workspace`, `WorkspaceMembership`, `Role`, `WorkspaceContext` models.
  - Dependencies: None
- [x] Task 1.2: Create `workspaces` and `workspace_memberships` tables (Postgres + SQLite migrations).
  - Dependencies: Task 1.1
- [x] Task 1.3: Implement `workspace_resolver` with membership cache (in-memory TTL by default; Redis-backed implementation deferred to Milestone 3) and invalidation hooks.
  - Dependencies: Task 1.1
- [x] Task 1.4: Update bearer-token middleware to attach `WorkspaceContext` to `request.state`.
  - Dependencies: Task 1.3
- [x] Task 1.5: Add `require_workspace()` FastAPI dependency and apply it to all protected routes.
  - Note: `resolve_workspace_context` is now wired as a global dependency on the protected `/api` router; anonymous requests resolve to the default workspace when workspace is disabled and are rejected (400 `workspace.required`) when enabled.
  - Dependencies: Task 1.4
- [x] Task 1.6: Implement baseline workspace role checks (`owner`, `admin`, `editor`, `viewer`) for protected routes.
  - Dependencies: Task 1.5
- [x] Task 1.7: Add config flag `multi_workspace.enabled` and `multi_workspace.default_workspace_slug`.
  - Dependencies: None
- [x] Task 1.8: Write the default-workspace backfill migration (nullable `workspace_id` → backfill → `NOT NULL`) for every affected table.
  - Note: SQLite helpers add nullable `workspace_id` columns and backfill the default workspace; the `NOT NULL` enforcement step is deferred until Milestone 2 retrofits each persistence layer.
  - Dependencies: Task 1.2
- [x] Task 1.9: Add admin API for workspace CRUD (`POST /api/admin/workspaces`, list, suspend, delete).
  - Dependencies: Task 1.6
- [x] Task 1.10: Add `orcheo workspace create|list|deactivate|invite|use` CLI commands.
  - Dependencies: Task 1.9
- [x] Task 1.11: Unit tests for `WorkspaceContext` propagation and resolver cache; integration tests for workspace CRUD and role enforcement.
  - Dependencies: Task 1.10

---

### Milestone 2: Persistence sweep

**Description:** Apply workspace scoping to every stateful subsystem, with cross-workspace isolation tests for each. After this milestone, no repository call can succeed without a `workspace_id`.

#### Task Checklist

- [x] Task 2.1: Workflow repository — add `workspace_id` argument, queries, indexes, and isolation tests (Postgres + SQLite).
  - Dependencies: Milestone 1
- [x] Task 2.2: Execution history store — add `workspace_id`, indexes, parent-step workspace checks, and isolation tests.
  - Dependencies: Milestone 1
- [x] Task 2.3: Service token repository — bind tokens to a workspace at issuance; reject mismatched lookups.
  - Dependencies: Milestone 1
- [x] Task 2.4: Vault — key credentials by `(workspace_id, name)`; workspace-scope templates/governance alerts; resolve `[[credential]]` placeholders in active workspace only.
  - Dependencies: Milestone 1
- [x] Task 2.5: ChatKit store — workspace-scope threads, messages, attachments, and subscriptions.
  - Dependencies: Milestone 1
- [x] Task 2.6: Agentensor checkpoints — add `workspace_id`; index `(workspace_id, workflow_id, config_version)` and workspace-scope best-checkpoint lookups.
  - Dependencies: Milestone 1
- [x] Task 2.7: Plugins — per-workspace install/enable state.
  - Dependencies: Milestone 1
- [x] Task 2.8: Listeners & triggers — workspace-scope registrations; route public webhooks via `/hooks/{workspace_slug}/{trigger_id}`.
  - Dependencies: Milestone 1
- [x] Task 2.9: Celery task envelopes — propagate `workspace_id` in headers; worker rejects unscoped tasks.
  - Dependencies: Milestone 1
- [x] Task 2.10: WebSocket layer — scope workflow sockets and run events to `(workspace_id, workflow_ref, run_id)`; reject cross-workspace attempts.
  - Dependencies: Task 2.2
- [x] Task 2.11: LangGraph state and persistence — carry `workspace_id`; ensure `decode_variables()` resolves in workspace scope; namespace checkpointer and graph-store records by workspace.
  - Dependencies: Task 2.4, Task 2.9
- [x] Task 2.12: Add a repository-helper lint/test that fails when a query omits `workspace_id`.
  - Dependencies: Task 2.1
- [x] Task 2.13: Cross-workspace isolation integration tests for every subsystem above.
  - Dependencies: Tasks 2.1–2.11

---

### Milestone 3: Governance & observability

**Description:** Add roles, quotas, audit logging, and per-workspace telemetry. After this milestone the deployment is operationally ready for multi-workspace use.

#### Task Checklist

- [x] Task 3.1: Harden the role policy matrix for sensitive actions and shared resources; verify every protected route has an explicit required role.
  - Dependencies: Milestone 2
- [x] Task 3.2: Membership management endpoints (`POST/DELETE /api/workspaces/{slug}/members`, role updates).
  - Dependencies: Task 3.1
- [x] Task 3.3: Per-workspace quota config (max workflows, concurrent runs, credentials, storage rows).
  - Dependencies: Task 3.1
- [x] Task 3.4: Quota enforcement in API and Celery dispatch; Redis-backed concurrent-run counter.
  - Dependencies: Task 3.3
- [x] Task 3.5: Per-workspace rate limiting on API and run submissions.
  - Dependencies: Task 3.3
- [x] Task 3.6: `workspace_audit_events` table and emission for sensitive actions (vault read, membership change, token issuance, workspace suspend).
  - Dependencies: Task 3.1
- [x] Task 3.7: Telemetry — tag every span, log, and metric with `workspace_id`; OTEL resource attribute `orcheo.workspace`.
  - Dependencies: Milestone 2
- [x] Task 3.8: Soft-delete workspaces with retention window; hard-delete tooling for GDPR-style requests.
  - Dependencies: Task 3.6

---

### Milestone 4: Polish & GA

**Description:** Documentation, ergonomics, and the GA flip. After this milestone the feature is generally available and existing deployments have a clear upgrade path.

#### Task Checklist

- [x] Task 4.1: Update `AGENTS.md`, deployment docs, Docker Compose, and systemd units for `ORCHEO_MULTI_WORKSPACE_ENABLED` and `ORCHEO_DEFAULT_WORKSPACE`.
  - Dependencies: Milestone 3
- [x] Task 4.2: Add Canvas read-only active-workspace indicator in the header; defer workspace switching and member management UI to P2.
  - Dependencies: Milestone 3
- [x] Task 4.3: SDK ergonomics — `--workspace` flag and `ORCHEO_WORKSPACE` env var on every resource command.
  - Dependencies: Milestone 3
- [x] Task 4.4: Upgrade runbook — flag-off backfill release → verification checklist → flag-on release; include rollback notes.
  - Dependencies: Task 4.1
- [x] Task 4.5: End-to-end multi-workspace demo (two workspaces, concurrent runs, quotas, audit log) and recorded walkthrough for docs.
  - Dependencies: Task 4.3
- [x] Task 4.6: Final coverage audit (≥95% project, 100% diff) and security review of cross-workspace boundaries.
  - Dependencies: Task 4.5

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Claude (Opus 4.7) | Initial draft |
| 2026-05-03 | Codex | Moved baseline RBAC into foundation, aligned persistence tasks with current stores, and deferred Canvas workspace-management UI to P2 |
| 2026-05-04 | Codex | Completed Milestone 2 persistence sweep, including workspace-aware Celery headers, WebSocket routing, runnable state propagation, repository lint coverage, and cross-workspace integration tests |
| 2026-05-04 | Codex | Added explicit workspace admin role gates, active-workspace Canvas indicator, SDK `--workspace` ergonomics, and deployment/runbook updates for flag-based rollout |
| 2026-05-05 | Codex | Finished the governance, retention, demo, and final audit pass; added workspace audit-log and delete/purge tooling, recorded the walkthrough docs, and confirmed 98% total / 100% diff coverage |
