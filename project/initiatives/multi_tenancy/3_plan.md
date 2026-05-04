# Project Plan

## For Multi-tenancy support for Orcheo

- **Version:** 0.1
- **Author:** Claude (Opus 4.7), Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

Deliver multi-tenancy support so one Orcheo deployment can serve multiple independent teams or individuals with strict logical isolation. Work is sequenced as foundation → persistence sweep → governance → polish, behind the `multi_tenancy.enabled` config flag, with tenant-aware RBAC and a backwards-compatible default-tenant upgrade path.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

## Status (2026-05-04)

- **Milestone 1 — Foundation:** complete. Tenancy core, settings, in-memory + SQLite repositories, resolver with TTL membership cache, admin/member API routes, `orcheo tenant` CLI, default-tenant bootstrap, and tenant-context-aware FastAPI dependency are all wired. The `/api` protected router globally resolves a `TenantContext` (anonymous principals fall through to the default tenant when `MULTI_TENANCY_ENABLED=False`; explicit 400 when enabled). Lint/mypy/tests all pass (5,898 tests, 60 tenancy unit + integration tests).
- **Milestone 2 — Persistence sweep:** in progress (8/13 tasks done). Task 2.1 complete: workflow repository and run repository have `tenant_id` columns, queries, migration logic (SQLite + Postgres), NULL=unscoped backward-compat semantics, router-level scoping for all 14 workflow/run/trigger/listener endpoints, and 7 cross-tenant isolation tests. Task 2.2 complete: `RunHistoryRecord.tenant_id` field, `start_run(tenant_id=)` param across InMemory/SQLite/Postgres stores, `list_histories(tenant_id=)` filter with NULL=unscoped semantics, SQLite migration, router passes `tenant_id` from tenant context, 8 cross-tenant isolation tests. Task 2.3 complete: service tokens already bound to tenant at issuance. Task 2.4 complete: `CredentialMetadata.tenant_id` field, per-tenant name uniqueness, `list_credentials`/`list_all_credentials` both accept `tenant_id` filter with NULL=unscoped semantics, SQLite migration, credential router passes `tenant_id` from `TenantContextDep` for list and create, 10 vault isolation tests. Task 2.5 complete: `tenant_id` column added to `chat_threads` (SQLite + Postgres schemas + SQLite migration), `ChatKitRequestContext` includes `tenant_id`, `ChatKitAuthResult` includes `tenant_id` fetched from `get_workflow_tenant_id()` (new protocol method, implemented in InMemory/SQLite/Postgres repos), context populated in chatkit router, `save_thread` stores `tenant_id`, `load_threads` filters by `tenant_id` with NULL=unscoped semantics in InMemory/SQLite/Postgres stores, 7 isolation tests. Task 2.6 complete: `AgentensorCheckpoint.tenant_id` field, `record_checkpoint(tenant_id=)` and `list_checkpoints(tenant_id=)` params across InMemory/SQLite/Postgres stores, NULL=unscoped semantics, SQLite migration, Postgres schema updated, agentensor router passes `tenant_id` from `TenantContextDep`, 8 isolation tests. Remaining 7 subsystems pending.
- **Milestone 3 / 4:** blocked on Milestone 2.

---

## Milestones

### Milestone 1: Foundation

**Description:** Establish the tenancy core (models, context, resolver), tenant-aware authentication, baseline role checks, and the default-tenant migration. After this milestone the codebase carries `tenant_id` end-to-end while behavior remains identical for existing single-tenant deployments.

#### Task Checklist

- [x] Task 1.1: Add `orcheo.tenancy` package with `Tenant`, `TenantMembership`, `Role`, `TenantContext` models.
  - Dependencies: None
- [x] Task 1.2: Create `tenants` and `tenant_memberships` tables (Postgres + SQLite migrations).
  - Dependencies: Task 1.1
- [x] Task 1.3: Implement `tenant_resolver` with membership cache (in-memory TTL by default; Redis-backed implementation deferred to Milestone 3) and invalidation hooks.
  - Dependencies: Task 1.1
- [x] Task 1.4: Update bearer-token middleware to attach `TenantContext` to `request.state`.
  - Dependencies: Task 1.3
- [x] Task 1.5: Add `require_tenant()` FastAPI dependency and apply it to all protected routes.
  - Note: `resolve_tenant_context` is now wired as a global dependency on the protected `/api` router; anonymous requests resolve to the default tenant when tenancy is disabled and are rejected (400 `tenant.required`) when enabled.
  - Dependencies: Task 1.4
- [x] Task 1.6: Implement baseline tenant role checks (`owner`, `admin`, `editor`, `viewer`) for protected routes.
  - Dependencies: Task 1.5
- [x] Task 1.7: Add config flag `multi_tenancy.enabled` and `multi_tenancy.default_tenant_slug`.
  - Dependencies: None
- [x] Task 1.8: Write the default-tenant backfill migration (nullable `tenant_id` → backfill → `NOT NULL`) for every affected table.
  - Note: SQLite helpers add nullable `tenant_id` columns and backfill the default tenant; the `NOT NULL` enforcement step is deferred until Milestone 2 retrofits each persistence layer.
  - Dependencies: Task 1.2
- [x] Task 1.9: Add admin API for tenant CRUD (`POST /api/admin/tenants`, list, suspend, delete).
  - Dependencies: Task 1.6
- [x] Task 1.10: Add `orcheo tenant create|list|deactivate|invite|use` CLI commands.
  - Dependencies: Task 1.9
- [x] Task 1.11: Unit tests for `TenantContext` propagation and resolver cache; integration tests for tenant CRUD and role enforcement.
  - Dependencies: Task 1.10

---

### Milestone 2: Persistence sweep

**Description:** Apply tenant scoping to every stateful subsystem, with cross-tenant isolation tests for each. After this milestone, no repository call can succeed without a `tenant_id`.

#### Task Checklist

- [x] Task 2.1: Workflow repository — add `tenant_id` argument, queries, indexes, and isolation tests (Postgres + SQLite).
  - Dependencies: Milestone 1
- [x] Task 2.2: Execution history store — add `tenant_id`, indexes, parent-step tenancy checks, and isolation tests.
  - Dependencies: Milestone 1
- [x] Task 2.3: Service token repository — bind tokens to a tenant at issuance; reject mismatched lookups.
  - Dependencies: Milestone 1
- [x] Task 2.4: Vault — key credentials by `(tenant_id, name)`; tenant-scope templates/governance alerts; resolve `[[credential]]` placeholders in active tenant only.
  - Dependencies: Milestone 1
- [x] Task 2.5: ChatKit store — tenant-scope threads, messages, attachments, and subscriptions.
  - Dependencies: Milestone 1
- [x] Task 2.6: Agentensor checkpoints — add `tenant_id`; index `(tenant_id, workflow_id, config_version)` and tenant-scope best-checkpoint lookups.
  - Dependencies: Milestone 1
- [x] Task 2.7: Plugins — per-tenant install/enable state.
  - Dependencies: Milestone 1
- [x] Task 2.8: Listeners & triggers — tenant-scope registrations; route public webhooks via `/hooks/{tenant_slug}/{trigger_id}`.
  - Dependencies: Milestone 1
- [ ] Task 2.9: Celery task envelopes — propagate `tenant_id` in headers; worker rejects unscoped tasks.
  - Dependencies: Milestone 1
- [ ] Task 2.10: WebSocket layer — scope workflow sockets and run events to `(tenant_id, workflow_ref, run_id)`; reject cross-tenant attempts.
  - Dependencies: Task 2.2
- [ ] Task 2.11: LangGraph state and persistence — carry `tenant_id`; ensure `decode_variables()` resolves in tenant scope; namespace checkpointer and graph-store records by tenant.
  - Dependencies: Task 2.4, Task 2.9
- [ ] Task 2.12: Add a repository-helper lint/test that fails when a query omits `tenant_id`.
  - Dependencies: Task 2.1
- [ ] Task 2.13: Cross-tenant isolation integration tests for every subsystem above.
  - Dependencies: Tasks 2.1–2.11

---

### Milestone 3: Governance & observability

**Description:** Add roles, quotas, audit logging, and per-tenant telemetry. After this milestone the deployment is operationally ready for multi-tenant use.

#### Task Checklist

- [ ] Task 3.1: Harden the role policy matrix for sensitive actions and shared resources; verify every protected route has an explicit required role.
  - Dependencies: Milestone 2
- [ ] Task 3.2: Membership management endpoints (`POST/DELETE /api/tenants/{slug}/members`, role updates).
  - Dependencies: Task 3.1
- [ ] Task 3.3: Per-tenant quota config (max workflows, concurrent runs, credentials, storage rows).
  - Dependencies: Task 3.1
- [ ] Task 3.4: Quota enforcement in API and Celery dispatch; Redis-backed concurrent-run counter.
  - Dependencies: Task 3.3
- [ ] Task 3.5: Per-tenant rate limiting on API and run submissions.
  - Dependencies: Task 3.3
- [ ] Task 3.6: `tenant_audit_events` table and emission for sensitive actions (vault read, membership change, token issuance, tenant suspend).
  - Dependencies: Task 3.1
- [ ] Task 3.7: Telemetry — tag every span, log, and metric with `tenant_id`; OTEL resource attribute `orcheo.tenant`.
  - Dependencies: Milestone 2
- [ ] Task 3.8: Soft-delete tenants with retention window; hard-delete tooling for GDPR-style requests.
  - Dependencies: Task 3.6

---

### Milestone 4: Polish & GA

**Description:** Documentation, ergonomics, and the GA flip. After this milestone the feature is generally available and existing deployments have a clear upgrade path.

#### Task Checklist

- [ ] Task 4.1: Update `AGENTS.md`, deployment docs, Docker Compose, and systemd units for `ORCHEO_MULTI_TENANCY_ENABLED` and `ORCHEO_DEFAULT_TENANT`.
  - Dependencies: Milestone 3
- [ ] Task 4.2: Add Canvas read-only active-tenant indicator in the header; defer tenant switching and member management UI to P2.
  - Dependencies: Milestone 3
- [ ] Task 4.3: SDK ergonomics — `--tenant` flag and `ORCHEO_TENANT` env var on every resource command.
  - Dependencies: Milestone 3
- [ ] Task 4.4: Upgrade runbook — flag-off backfill release → verification checklist → flag-on release; include rollback notes.
  - Dependencies: Task 4.1
- [ ] Task 4.5: End-to-end multi-tenant demo (two tenants, concurrent runs, quotas, audit log) and recorded walkthrough for docs.
  - Dependencies: Task 4.3
- [ ] Task 4.6: Final coverage audit (≥95% project, 100% diff) and security review of cross-tenant boundaries.
  - Dependencies: Task 4.5

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Claude (Opus 4.7) | Initial draft |
| 2026-05-03 | Codex | Moved baseline RBAC into foundation, aligned persistence tasks with current stores, and deferred Canvas tenant-management UI to P2 |
