# Requirements Document

## METADATA
- **Authors:** Claude (Opus 4.7), Codex
- **Project/Feature Name:** Multi-tenancy support for Orcheo
- **Type:** Feature
- **Summary:** Introduce a tenant-scoped data and execution model so a single Orcheo deployment can serve multiple independent teams or individuals without data leakage, quota interference, or operational coupling.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-05-03

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | ShaojieJiang | Multi-tenancy Design |
| Project Plan | `./3_plan.md` | ShaojieJiang | Multi-tenancy Plan |
| Repository Guidelines | `../../../AGENTS.md` | ShaojieJiang | Agents Guidelines |
| Persistence Layer | `src/orcheo/persistence.py` | ShaojieJiang | Persistence Module |
| Vault | `src/orcheo/vault/` | ShaojieJiang | Credential Vault |
| Workflow Repository | `apps/backend/src/orcheo_backend/app/repository/` | ShaojieJiang | Backend Repository |
| History Store | `apps/backend/src/orcheo_backend/app/history/` | ShaojieJiang | Execution History Store |
| Service Tokens | `apps/backend/src/orcheo_backend/app/service_token_repository/` | ShaojieJiang | Service Token Repository |
| ChatKit Store | `apps/backend/src/orcheo_backend/app/chatkit_store_sqlite/`, `apps/backend/src/orcheo_backend/app/chatkit_store_postgres/` | ShaojieJiang | ChatKit Persistence |

## PROBLEM DEFINITION
### Objectives
Allow one Orcheo deployment to host multiple independent teams or individuals on shared infrastructure with strict logical isolation. Provide tenant-aware authentication, authorization, persistence, execution, and observability.

### Target users
Self-hosted operators running Orcheo for several teams or clients; SaaS-style hosts exposing Orcheo to multiple end-users; individual users who want isolated workspaces within a shared deployment.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform operator | provision tenants on a single deployment | I can serve multiple teams without spinning up separate stacks | P0 | Tenants can be created, listed, and deactivated via CLI/API; tenant data is isolated end-to-end |
| Tenant admin | invite users into my tenant and assign roles | only authorized members can access my workflows and credentials | P0 | Membership and role assignment APIs exist; access checks enforce roles on every protected route |
| Developer | author workflows that only see my tenant's credentials and data | another tenant cannot read or modify my work | P0 | Workflow repository, execution history, vault, and chat data are partitioned by `tenant_id`; cross-tenant access returns 404 |
| Operator | apply per-tenant quotas (workflows, concurrent runs, storage) | a noisy tenant cannot exhaust shared resources | P1 | Quota config is enforced; exceeding limits returns a clear error; metrics are emitted per tenant |
| Operator | view per-tenant usage and audit logs | I can bill, debug, or investigate abuse | P1 | Telemetry and execution history are tagged with `tenant_id`; dashboards filter by tenant |
| Existing single-tenant user | upgrade my deployment without data loss | I can adopt multi-tenancy when ready | P0 | Migration assigns existing data to a default tenant; operators can roll back behavior by disabling multi-tenancy while only the default tenant exists |

### Context, Problems, Opportunities
Orcheo today assumes a single-tenant deployment: workflows, credentials, execution history, chat threads, listeners, and service tokens share a flat namespace. Operators who want to host more than one team must run separate stacks per team, which increases cost, fragments observability, and prevents shared infrastructure (Postgres, Redis, Celery worker pool). Adding a first-class `tenant_id` to identity, persistence, execution, and telemetry unlocks shared-deployment scenarios while keeping logical isolation strict. This also lays groundwork for a managed/SaaS offering and for fine-grained per-team governance.

### Product goals and Non-goals
Goals:
- First-class tenant identity propagated end-to-end (API, runtime, persistence, telemetry).
- Strict logical isolation across all stateful subsystems.
- Tenant-aware AuthN/AuthZ with roles inside a tenant.
- Per-tenant quotas and usage visibility.
- Backwards-compatible upgrade path for existing single-tenant deployments.

Non-goals:
- Hard physical isolation (separate databases, separate worker pools per tenant). Out of scope for v1.
- Cross-tenant sharing of workflows or credentials.
- Billing, invoicing, or payment integration.
- A new tenant-management UI in Canvas (CLI/API only for v1).
- Replacing the existing auth model with a full IdP/SSO product.

## PRODUCT DEFINITION
### Requirements
P0:
- Add `tenants` and `tenant_memberships` tables; introduce a `Tenant` model and a `TenantContext` carried through requests, runtime, and Celery tasks.
- Propagate `tenant_id` to every stateful subsystem: workflow repository, workflow versions/runs, execution history, service tokens, vault credentials/templates/governance alerts, ChatKit store, Agentensor checkpoints, plugin install state, listeners, triggers, retry policies, and LangGraph checkpoint/store persistence.
- Add `tenant_id` to every persistence schema (Postgres + SQLite) with composite indexes on `(tenant_id, ...)` for hot lookups.
- Tenant-aware authentication: bearer tokens and service tokens resolve to a `(user_id, tenant_id)` pair; per-request middleware rejects unscoped traffic.
- Roles inside a tenant: `owner`, `admin`, `editor`, `viewer`, with route-level checks.
- Tenant-aware CLI commands (`orcheo tenant create|list|deactivate|invite|use`).
- Migration: existing rows are assigned to a `default` tenant; runtime behavior is gated by a config flag and can be rolled back to default-tenant behavior while no second tenant exists. Schema migrations are forward-only unless a dedicated downgrade is added.
- Test coverage ≥95% project, 100% diff; integration tests cover cross-tenant isolation for every subsystem.

P1:
- Per-tenant quotas: max workflows, max concurrent runs, max storage rows, max credentials.
- Per-tenant rate limiting on API and execution submissions.
- Telemetry: every span, log, and metric is tagged with `tenant_id`; OTEL resource attribute `orcheo.tenant`.
- Tenant-scoped audit log for sensitive actions (membership change, vault read, token issuance).
- Soft-delete of tenants with retention window; hard-delete tooling for GDPR-style requests.

P2:
- Canvas UI for tenant switching and member management.
- Per-tenant feature flags.
- Tenant-scoped plugin allowlists.
- BYO-secret-store per tenant (e.g., per-tenant KMS key for vault).

### Designs (if applicable)
Design doc: `./2_design.md`. No Canvas UI for v1; CLI/API only.

### [Optional] Other Teams Impacted
- Backend: every persistence subsystem requires a schema and query change.
- SDK: `orcheo` and `horcheo` CLIs gain tenant-aware commands and a `--tenant` flag.
- Canvas: WebSocket and REST clients must send tenant context; minimal UI changes required for v1 (read-only header indicating active tenant).
- DevOps: deployment docs, Docker Compose, and systemd units gain `ORCHEO_MULTI_TENANCY_ENABLED` and `ORCHEO_DEFAULT_TENANT` knobs.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Introduce a `TenantContext` value object created by auth middleware from the bearer token, propagated through FastAPI dependencies, the LangGraph state, Celery task headers, and persistence calls. All repositories accept `tenant_id` as a required argument; protocol signatures are updated and backends enforce the predicate at the SQL layer. A central `tenant_resolver` resolves principals to tenant memberships and caches them in Redis with short TTL. Cross-cutting concerns (telemetry, quotas, audit) read from `TenantContext`.

### Technical Requirements
- Required tenancy field on every persisted row touched by tenant-owned data.
- All queries must filter by `tenant_id`; lint rule or repository helper to make omission a build error.
- Postgres and SQLite migrations use idempotent, versioned schema helpers consistent with the existing repository, history, ChatKit, service-token, and vault stores.
- Service tokens are scoped to one tenant at issuance time; tokens cannot span tenants.
- Vault credentials are keyed by `(tenant_id, name)`; existing `[[credential_name]]` placeholders resolve only within the active tenant.
- Celery tasks must pass `tenant_id` in task headers; workers reject tasks lacking it.
- WebSocket subscriptions are scoped to `(tenant_id, workflow_ref, run_id)`; cross-tenant subscription attempts are rejected.
- ≥95% project coverage and 100% diff coverage; integration tests assert isolation by attempting cross-tenant reads/writes.

### AI/ML Considerations (if applicable)
Not applicable. AI nodes inherit tenant context from the run; LLM calls are unchanged but per-tenant API key resolution flows through the tenant-scoped vault.

## MARKET DEFINITION (for products or large features)
Not applicable. Internal feature targeting self-hosted and SaaS-style deployments of Orcheo.

## LAUNCH/ROLLOUT PLAN
### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Cross-tenant isolation defects in pre-prod | 0 — any leak is a release blocker |
| [Primary] Subsystems with tenant scoping | 100% of stateful subsystems migrated and tested |
| [Secondary] Single-tenant upgrade success | 100% of existing deployments migrate without data loss |
| [Secondary] Per-tenant p95 API latency overhead | < 5 ms vs. single-tenant baseline |
| [Guardrail] Coverage | ≥95% project, 100% diff on tenancy-touching code |

### Rollout Strategy
Behind a single configuration flag `multi_tenancy.enabled`. With the flag off, the deployment behaves as today and all data is implicitly assigned to the `default` tenant. With the flag on, every API call must resolve to a tenant or be rejected. Existing operators upgrade in two steps: deploy the flag-off release (adds `tenant_id` columns and backfills `default`), then deploy the flag-on release after verifying data integrity.

### Experiment Plan (if applicable)
Not applicable.

### Estimated Launch Phases (if applicable)
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Foundation | Tenant model, identity, context propagation, default-tenant migration |
| **Phase 2** | Persistence | All stateful subsystems gain `tenant_id` enforcement and tests |
| **Phase 3** | Governance | Roles, quotas, audit log, telemetry tagging |
| **Phase 4** | Polish | CLI ergonomics, docs, Canvas read-only tenant indicator |

## HYPOTHESIS & RISKS
Hypothesis: Adding a tenant boundary at identity, persistence, execution, and telemetry lets one Orcheo deployment serve multiple independent teams safely, with negligible latency overhead and a smooth upgrade path for existing single-tenant users.

Risks:
- A missed query or repository call leaks data across tenants.
- Migration to add `tenant_id` columns is slow or unsafe on large existing deployments.
- Celery task fan-out drops tenant context, allowing tasks to read another tenant's data.
- Per-tenant quotas misfire and starve legitimate workloads.

Risk Mitigation:
- Centralize tenant filtering inside repository helpers; add a lint/test that fails when a query omits `tenant_id`.
- Run schema migrations with online-safe patterns (nullable column → backfill → `NOT NULL`); document behavioral rollback via the feature flag and call out any unsupported destructive downgrade.
- Enforce `tenant_id` in Celery task headers and reject tasks without it; cover with integration tests.
- Default quotas to generous values and emit warning metrics before hard-rejecting; allow per-tenant override.

## APPENDIX
- Stateful subsystems requiring tenancy: workflow repository, workflow versions/runs, execution history and steps, service tokens and audit log, vault credentials/templates/governance alerts, ChatKit threads/messages/attachments, Agentensor checkpoints, plugin install state, listeners/cursors/dedupe, webhook/cron triggers, retry policies, scheduled tasks, LangGraph checkpoints, and graph store.
- Identity surfaces requiring tenancy: bearer token middleware, service tokens, WebSocket auth, CLI auth, Celery task headers.
- Existing single-tenant data is assigned to a tenant named `default` (slug `default`).
