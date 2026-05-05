# Requirements Document

## METADATA
- **Authors:** Claude (Opus 4.7), Codex
- **Project/Feature Name:** Multi-workspace support for Orcheo
- **Type:** Feature
- **Summary:** Introduce a workspace-scoped data and execution model so a single Orcheo deployment can serve multiple independent teams or individuals without data leakage, quota interference, or operational coupling.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-05-03

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | ShaojieJiang | Multi-workspace Design |
| Project Plan | `./3_plan.md` | ShaojieJiang | Multi-workspace Plan |
| Repository Guidelines | `../../../AGENTS.md` | ShaojieJiang | Agents Guidelines |
| Persistence Layer | `src/orcheo/persistence.py` | ShaojieJiang | Persistence Module |
| Vault | `src/orcheo/vault/` | ShaojieJiang | Credential Vault |
| Workflow Repository | `apps/backend/src/orcheo_backend/app/repository/` | ShaojieJiang | Backend Repository |
| History Store | `apps/backend/src/orcheo_backend/app/history/` | ShaojieJiang | Execution History Store |
| Service Tokens | `apps/backend/src/orcheo_backend/app/service_token_repository/` | ShaojieJiang | Service Token Repository |
| ChatKit Store | `apps/backend/src/orcheo_backend/app/chatkit_store_sqlite/`, `apps/backend/src/orcheo_backend/app/chatkit_store_postgres/` | ShaojieJiang | ChatKit Persistence |

## PROBLEM DEFINITION
### Objectives
Allow one Orcheo deployment to host multiple independent teams or individuals on shared infrastructure with strict logical isolation. Provide workspace-aware authentication, authorization, persistence, execution, and observability.

### Target users
Self-hosted operators running Orcheo for several teams or clients; SaaS-style hosts exposing Orcheo to multiple end-users; individual users who want isolated workspaces within a shared deployment.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform operator | provision workspaces on a single deployment | I can serve multiple teams without spinning up separate stacks | P0 | Workspaces can be created, listed, and deactivated via CLI/API; workspace data is isolated end-to-end |
| Workspace admin | invite users into my workspace and assign roles | only authorized members can access my workflows and credentials | P0 | Membership and role assignment APIs exist; access checks enforce roles on every protected route |
| Developer | author workflows that only see my workspace's credentials and data | another workspace cannot read or modify my work | P0 | Workflow repository, execution history, vault, and chat data are partitioned by `workspace_id`; cross-workspace access returns 404 |
| Operator | apply per-workspace quotas (workflows, concurrent runs, storage) | a noisy workspace cannot exhaust shared resources | P1 | Quota config is enforced; exceeding limits returns a clear error; metrics are emitted per workspace |
| Operator | view per-workspace usage and audit logs | I can bill, debug, or investigate abuse | P1 | Telemetry and execution history are tagged with `workspace_id`; dashboards filter by workspace |
| Existing single-workspace user | upgrade my deployment without data loss | I can adopt multi-workspace when ready | P0 | Migration assigns existing data to a default workspace; operators can roll back behavior by disabling multi-workspace while only the default workspace exists |

### Context, Problems, Opportunities
Orcheo today assumes a single-workspace deployment: workflows, credentials, execution history, chat threads, listeners, and service tokens share a flat namespace. Operators who want to host more than one team must run separate stacks per team, which increases cost, fragments observability, and prevents shared infrastructure (Postgres, Redis, Celery worker pool). Adding a first-class `workspace_id` to identity, persistence, execution, and telemetry unlocks shared-deployment scenarios while keeping logical isolation strict. This also lays groundwork for a managed/SaaS offering and for fine-grained per-team governance.

### Product goals and Non-goals
Goals:
- First-class workspace identity propagated end-to-end (API, runtime, persistence, telemetry).
- Strict logical isolation across all stateful subsystems.
- Workspace-aware AuthN/AuthZ with roles inside a workspace.
- Per-workspace quotas and usage visibility.
- Backwards-compatible upgrade path for existing single-workspace deployments.

Non-goals:
- Hard physical isolation (separate databases, separate worker pools per workspace). Out of scope for v1.
- Cross-workspace sharing of workflows or credentials.
- Billing, invoicing, or payment integration.
- A new workspace-management UI in Canvas (CLI/API only for v1).
- Replacing the existing auth model with a full IdP/SSO product.

## PRODUCT DEFINITION
### Requirements
P0:
- Add `workspaces` and `workspace_memberships` tables; introduce a `Workspace` model and a `WorkspaceContext` carried through requests, runtime, and Celery tasks.
- Propagate `workspace_id` to every stateful subsystem: workflow repository, workflow versions/runs, execution history, service tokens, vault credentials/templates/governance alerts, ChatKit store, Agentensor checkpoints, plugin install state, listeners, triggers, retry policies, and LangGraph checkpoint/store persistence.
- Add `workspace_id` to every persistence schema (Postgres + SQLite) with composite indexes on `(workspace_id, ...)` for hot lookups.
- Workspace-aware authentication: bearer tokens and service tokens resolve to a `(user_id, workspace_id)` pair; per-request middleware rejects unscoped traffic.
- Roles inside a workspace: `owner`, `admin`, `editor`, `viewer`, with route-level checks.
- Workspace-aware CLI commands (`orcheo workspace create|list|deactivate|invite|use`).
- Migration: existing rows are assigned to a `default` workspace; runtime behavior is gated by a config flag and can be rolled back to default-workspace behavior while no second workspace exists. Schema migrations are forward-only unless a dedicated downgrade is added.
- Test coverage ≥95% project, 100% diff; integration tests cover cross-workspace isolation for every subsystem.

P1:
- Per-workspace quotas: max workflows, max concurrent runs, max storage rows, max credentials.
- Per-workspace rate limiting on API and execution submissions.
- Telemetry: every span, log, and metric is tagged with `workspace_id`; OTEL resource attribute `orcheo.workspace`.
- Workspace-scoped audit log for sensitive actions (membership change, vault read, token issuance).
- Soft-delete of workspaces with retention window; hard-delete tooling for GDPR-style requests.

P2:
- Canvas UI for workspace switching and member management.
- Per-workspace feature flags.
- Workspace-scoped plugin allowlists.
- BYO-secret-store per workspace (e.g., per-workspace KMS key for vault).

### Designs (if applicable)
Design doc: `./2_design.md`. No Canvas UI for v1; CLI/API only.

### [Optional] Other Teams Impacted
- Backend: every persistence subsystem requires a schema and query change.
- SDK: `orcheo` and `horcheo` CLIs gain workspace-aware commands and a `--workspace` flag.
- Canvas: WebSocket and REST clients must send workspace context; minimal UI changes required for v1 (read-only header indicating active workspace).
- DevOps: deployment docs, Docker Compose, and systemd units gain `ORCHEO_MULTI_WORKSPACE_ENABLED` and `ORCHEO_DEFAULT_WORKSPACE` knobs.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Introduce a `WorkspaceContext` value object created by auth middleware from the bearer token, propagated through FastAPI dependencies, the LangGraph state, Celery task headers, and persistence calls. All repositories accept `workspace_id` as a required argument; protocol signatures are updated and backends enforce the predicate at the SQL layer. A central `workspace_resolver` resolves principals to workspace memberships and caches them in Redis with short TTL. Cross-cutting concerns (telemetry, quotas, audit) read from `WorkspaceContext`.

### Technical Requirements
- Required workspace field on every persisted row touched by workspace-owned data.
- All queries must filter by `workspace_id`; lint rule or repository helper to make omission a build error.
- Postgres and SQLite migrations use idempotent, versioned schema helpers consistent with the existing repository, history, ChatKit, service-token, and vault stores.
- Service tokens are scoped to one workspace at issuance time; tokens cannot span workspaces.
- Vault credentials are keyed by `(workspace_id, name)`; existing `[[credential_name]]` placeholders resolve only within the active workspace.
- Celery tasks must pass `workspace_id` in task headers; workers reject tasks lacking it.
- WebSocket subscriptions are scoped to `(workspace_id, workflow_ref, run_id)`; cross-workspace subscription attempts are rejected.
- ≥95% project coverage and 100% diff coverage; integration tests assert isolation by attempting cross-workspace reads/writes.

### AI/ML Considerations (if applicable)
Not applicable. AI nodes inherit workspace context from the run; LLM calls are unchanged but per-workspace API key resolution flows through the workspace-scoped vault.

## MARKET DEFINITION (for products or large features)
Not applicable. Internal feature targeting self-hosted and SaaS-style deployments of Orcheo.

## LAUNCH/ROLLOUT PLAN
### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Cross-workspace isolation defects in pre-prod | 0 — any leak is a release blocker |
| [Primary] Subsystems with workspace scoping | 100% of stateful subsystems migrated and tested |
| [Secondary] Single-workspace upgrade success | 100% of existing deployments migrate without data loss |
| [Secondary] Per-workspace p95 API latency overhead | < 5 ms vs. single-workspace baseline |
| [Guardrail] Coverage | ≥95% project, 100% diff on workspace-touching code |

### Rollout Strategy
Behind a single configuration flag `multi_workspace.enabled`. With the flag off, the deployment behaves as today and all data is implicitly assigned to the `default` workspace. With the flag on, every API call must resolve to a workspace or be rejected. Existing operators upgrade in two steps: deploy the flag-off release (adds `workspace_id` columns and backfills `default`), then deploy the flag-on release after verifying data integrity.

### Experiment Plan (if applicable)
Not applicable.

### Estimated Launch Phases (if applicable)
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Foundation | Workspace model, identity, context propagation, default-workspace migration |
| **Phase 2** | Persistence | All stateful subsystems gain `workspace_id` enforcement and tests |
| **Phase 3** | Governance | Roles, quotas, audit log, telemetry tagging |
| **Phase 4** | Polish | CLI ergonomics, docs, Canvas read-only workspace indicator |

## HYPOTHESIS & RISKS
Hypothesis: Adding a workspace boundary at identity, persistence, execution, and telemetry lets one Orcheo deployment serve multiple independent teams safely, with negligible latency overhead and a smooth upgrade path for existing single-workspace users.

Risks:
- A missed query or repository call leaks data across workspaces.
- Migration to add `workspace_id` columns is slow or unsafe on large existing deployments.
- Celery task fan-out drops workspace context, allowing tasks to read another workspace's data.
- Per-workspace quotas misfire and starve legitimate workloads.

Risk Mitigation:
- Centralize workspace filtering inside repository helpers; add a lint/test that fails when a query omits `workspace_id`.
- Run schema migrations with online-safe patterns (nullable column → backfill → `NOT NULL`); document behavioral rollback via the feature flag and call out any unsupported destructive downgrade.
- Enforce `workspace_id` in Celery task headers and reject tasks without it; cover with integration tests.
- Default quotas to generous values and emit warning metrics before hard-rejecting; allow per-workspace override.

## APPENDIX
- Stateful subsystems requiring workspace: workflow repository, workflow versions/runs, execution history and steps, service tokens and audit log, vault credentials/templates/governance alerts, ChatKit threads/messages/attachments, Agentensor checkpoints, plugin install state, listeners/cursors/dedupe, webhook/cron triggers, retry policies, scheduled tasks, LangGraph checkpoints, and graph store.
- Identity surfaces requiring workspace: bearer token middleware, service tokens, WebSocket auth, CLI auth, Celery task headers.
- Existing single-workspace data is assigned to a workspace named `default` (slug `default`).
