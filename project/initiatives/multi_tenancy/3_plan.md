# Project Plan

## For Multi-tenancy support for Orcheo

- **Version:** 0.1
- **Author:** Claude (Opus 4.7)
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

Deliver multi-tenancy support so one Orcheo deployment can serve multiple independent teams or individuals with strict logical isolation. Work is sequenced as foundation → persistence sweep → governance → polish, behind the `multi_tenancy.enabled` config flag, with a backwards-compatible default-tenant upgrade path.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 1: Foundation

**Description:** Establish the tenancy core (models, context, resolver), tenant-aware authentication, and the default-tenant migration. After this milestone the codebase carries `tenant_id` end-to-end while behavior remains identical for existing single-tenant deployments.

#### Task Checklist

- [ ] Task 1.1: Add `orcheo.tenancy` package with `Tenant`, `TenantMembership`, `Role`, `TenantContext` models.
  - Dependencies: None
- [ ] Task 1.2: Create `tenants` and `tenant_memberships` tables (Postgres + SQLite migrations).
  - Dependencies: Task 1.1
- [ ] Task 1.3: Implement `tenant_resolver` with Redis-backed membership cache and invalidation hooks.
  - Dependencies: Task 1.1
- [ ] Task 1.4: Update bearer-token middleware to attach `TenantContext` to `request.state`.
  - Dependencies: Task 1.3
- [ ] Task 1.5: Add `require_tenant()` FastAPI dependency and apply it to all protected routes.
  - Dependencies: Task 1.4
- [ ] Task 1.6: Add config flag `multi_tenancy.enabled` and `multi_tenancy.default_tenant_slug`.
  - Dependencies: None
- [ ] Task 1.7: Write the default-tenant backfill migration (nullable `tenant_id` → backfill → `NOT NULL`) for every affected table.
  - Dependencies: Task 1.2
- [ ] Task 1.8: Add admin API for tenant CRUD (`POST /api/admin/tenants`, list, suspend, delete).
  - Dependencies: Task 1.5
- [ ] Task 1.9: Add `orcheo tenant create|list|deactivate|invite|use` CLI commands.
  - Dependencies: Task 1.8
- [ ] Task 1.10: Unit tests for `TenantContext` propagation and resolver cache; integration tests for tenant CRUD.
  - Dependencies: Task 1.9

---

### Milestone 2: Persistence sweep

**Description:** Apply tenant scoping to every stateful subsystem, with cross-tenant isolation tests for each. After this milestone, no repository call can succeed without a `tenant_id`.

#### Task Checklist

- [ ] Task 2.1: Workflow repository — add `tenant_id` argument, queries, indexes, and isolation tests (Postgres + SQLite).
  - Dependencies: Milestone 1
- [ ] Task 2.2: Run history store — add `tenant_id`, indexes, and isolation tests.
  - Dependencies: Milestone 1
- [ ] Task 2.3: Service token repository — bind tokens to a tenant at issuance; reject mismatched lookups.
  - Dependencies: Milestone 1
- [ ] Task 2.4: Vault — key entries by `(tenant_id, name)`; resolve `[[credential]]` placeholders in active tenant only.
  - Dependencies: Milestone 1
- [ ] Task 2.5: ChatKit store — tenant-scope threads, messages, attachments, and subscriptions.
  - Dependencies: Milestone 1
- [ ] Task 2.6: Agentensor checkpoints — tenant-scope JSONB metadata; GIN index on `tenant_id`.
  - Dependencies: Milestone 1
- [ ] Task 2.7: Plugins — per-tenant install/enable state.
  - Dependencies: Milestone 1
- [ ] Task 2.8: Listeners & triggers — tenant-scope registrations; route public webhooks via `/hooks/{tenant_slug}/{trigger_id}`.
  - Dependencies: Milestone 1
- [ ] Task 2.9: Celery task envelopes — propagate `tenant_id` in headers; worker rejects unscoped tasks.
  - Dependencies: Milestone 1
- [ ] Task 2.10: WebSocket layer — scope subscriptions to `(tenant_id, run_id)`; reject cross-tenant attempts.
  - Dependencies: Task 2.2
- [ ] Task 2.11: LangGraph state — carry `tenant_id`; ensure `decode_variables()` resolves in tenant scope.
  - Dependencies: Task 2.4, Task 2.9
- [ ] Task 2.12: Add a repository-helper lint/test that fails when a query omits `tenant_id`.
  - Dependencies: Task 2.1
- [ ] Task 2.13: Cross-tenant isolation integration tests for every subsystem above.
  - Dependencies: Tasks 2.1–2.11

---

### Milestone 3: Governance & observability

**Description:** Add roles, quotas, audit logging, and per-tenant telemetry. After this milestone the deployment is operationally ready for multi-tenant use.

#### Task Checklist

- [ ] Task 3.1: Implement role-based access checks (`owner`, `admin`, `editor`, `viewer`) on every protected route.
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

- [ ] Task 4.1: Update `AGENTS.md`, deployment docs, Docker Compose, and systemd units for multi-tenant config.
  - Dependencies: Milestone 3
- [ ] Task 4.2: Add Canvas read-only tenant indicator in the header; tenant switcher dropdown when the user has multiple memberships.
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
