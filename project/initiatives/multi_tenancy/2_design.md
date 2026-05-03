# Design Document

## For Multi-tenancy support for Orcheo

- **Version:** 0.1
- **Author:** Claude (Opus 4.7), Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

This design introduces tenant-scoped identity, persistence, execution, and telemetry to Orcheo so a single deployment can host multiple independent teams or individuals with strict logical isolation. A `TenantContext` is created by auth middleware from a bearer or service token and propagated through FastAPI dependencies, LangGraph state, Celery task headers, and the WebSocket layer. Every repository accepts `tenant_id` as a required argument and every persistence schema gains a `tenant_id` column with composite indexes on hot paths.

The design favors logical isolation in shared databases over physical separation. This keeps the operational surface small (one Postgres, one Redis, one worker pool), preserves Orcheo's existing protocol/factory patterns, and ships behind a single config flag with a backwards-compatible upgrade path that assigns existing data to a `default` tenant.

## Components

- **Tenancy core (`orcheo.tenancy`)**
  - `Tenant`, `TenantMembership`, `Role` models.
  - `TenantContext` value object (`tenant_id`, `user_id`, `role`, `quotas`).
  - `tenant_resolver` service: resolves principals to memberships, caches in Redis (TTL 60s).
  - Centralized `require_tenant()` FastAPI dependency.

- **Identity & Auth (`orcheo_backend.app.authentication`)**
  - Updates bearer token middleware to attach a `TenantContext` per request.
  - Service tokens carry `tenant_id` at issuance; validation rejects token-tenant mismatch.
  - WebSocket handshake requires a tenant-scoped token.

- **Workflow Repository (`orcheo_backend.app.repository`)**
  - `WorkflowRepository` methods gain an explicit `tenant_id` argument.
  - SQL queries filter by `tenant_id`; composite index `(tenant_id, handle)` and `(tenant_id, updated_at)`.
  - Helper `tenant_scoped(query, tenant_id)` enforces the predicate.

- **Execution History Store (`orcheo_backend.app.history`)**
  - Adds `tenant_id` to `execution_history` and tenant-checks `execution_history_steps` through the parent execution.
  - Read APIs require `tenant_id`; cross-tenant lookups return 404.

- **Service Tokens (`orcheo_backend.app.service_token_repository`)**
  - Tokens are issued for a single `tenant_id`; the column is `NOT NULL`.
  - Rotation and revocation operate within the issuing tenant only.

- **Vault (`orcheo.vault`)**
  - Credential keys become `(tenant_id, name)`; `[[credential_name]]` placeholders resolve in the active tenant only.
  - Per-tenant encryption key derivation (P2: BYO-KMS).

- **ChatKit Store (`orcheo_backend.app.chatkit_store_sqlite`, `orcheo_backend.app.chatkit_store_postgres`)**
  - Threads, messages, and attachments gain `tenant_id`; subscriptions are tenant-scoped.

- **Agentensor Checkpoints (`orcheo_backend.app.agentensor.checkpoint_store`)**
  - Checkpoints gain `tenant_id`; hot lookups use `(tenant_id, workflow_id, config_version)`.

- **Plugins (`orcheo.plugins`)**
  - Plugin install/enable state is per-tenant; allowlist enforced per tenant (P2 expansion).

- **Listeners & Triggers (`orcheo.listeners`, `orcheo.triggers`)**
  - Listener registrations, webhook endpoints, and cron triggers carry `tenant_id`.
  - Public webhook URLs include the tenant slug to avoid ambiguity (`/hooks/{tenant_slug}/{trigger_id}`).

- **Execution Worker (`orcheo_backend.worker`)**
  - Task envelopes carry `tenant_id` in headers; worker rejects unscoped tasks.
  - LangGraph state inherits `tenant_id`; node `decode_variables()` resolves variables in tenant scope.

- **LangGraph Persistence (`orcheo.persistence`)**
  - Checkpointer and graph-store namespaces include `tenant_id`; tenant-owned keys are never shared across tenants.

- **Telemetry (`orcheo.observability`, `orcheo.telemetry`, `orcheo.tracing`)**
  - OTEL resource attribute `orcheo.tenant`; metrics, logs, and spans tagged with `tenant_id`.
  - Audit log table `tenant_audit_events` for sensitive actions.

- **CLI (`packages/sdk/src/orcheo_sdk/cli`)**
  - `orcheo tenant create|list|deactivate`, `orcheo tenant invite`, `orcheo tenant use <slug>`.
  - All resource commands accept `--tenant <slug>` and read `ORCHEO_TENANT` env var.

## Request Flows

### Flow 1: Authenticated API request
1. Client sends `Authorization: Bearer <token>` and optional `X-Orcheo-Tenant: <slug>`.
2. Auth middleware validates the token and looks up principal memberships via `tenant_resolver`.
3. Middleware selects the tenant (from token claim, `X-Orcheo-Tenant` header, or principal's default).
4. `TenantContext` is attached to `request.state`; downstream `require_tenant()` dependency exposes it.
5. Route handler calls repositories with `tenant_id`; queries filter by it.

### Flow 2: Workflow execution
1. API receives `POST /api/workflows/{workflow_ref}/runs` with `tenant_id` from `TenantContext`.
2. Run is persisted with `tenant_id` and dispatched to Celery with `tenant_id` in task headers.
3. Worker rebuilds `TenantContext`, hydrates LangGraph state including `tenant_id`.
4. Nodes resolve variables and credentials via tenant-scoped vault.
5. Execution-history events stream via WebSocket scoped to `(tenant_id, run_id)`.

### Flow 3: Listener / webhook delivery
1. External service POSTs to `/hooks/{tenant_slug}/{trigger_id}`.
2. Trigger router resolves the slug to `tenant_id`; rejects unknown slugs.
3. Trigger handler enqueues a run in the resolved tenant; Celery task carries `tenant_id`.

### Flow 4: Tenant provisioning
1. Operator runs `orcheo tenant create --slug acme --owner-email alice@acme.io`.
2. CLI calls admin API with deployment admin token (super-admin scope).
3. Admin API creates the tenant row, default quotas, and an `owner` membership for the named user.
4. CLI prints the new tenant slug and an initial bootstrap service token.

### Flow 5: Single-tenant upgrade
1. Operator deploys release with `multi_tenancy.enabled=false` and `multi_tenancy.default_tenant_slug=default`.
2. Schema migration adds nullable `tenant_id` columns, backfills with the `default` tenant id, then sets `NOT NULL`.
3. After verification, operator flips `multi_tenancy.enabled=true`.
4. Subsequent requests must resolve to a tenant; absent header defaults to the principal's primary membership.

## API Contracts

```
POST /api/admin/tenants
Headers:
  Authorization: Bearer <super-admin-token>
Body:
  { "slug": "acme", "name": "Acme Inc", "owner_email": "alice@acme.io" }
Response:
  201 -> { "tenant_id": "uuid", "slug": "acme", "bootstrap_token": "<service-token>" }
  409 -> slug conflict
```

```
GET /api/tenants/me
Headers:
  Authorization: Bearer <user-token>
Response:
  200 -> { "memberships": [{ "tenant_id": "uuid", "slug": "acme", "role": "editor" }, ...] }
```

```
POST /api/tenants/{slug}/members
Headers:
  Authorization: Bearer <user-token>   # must be admin/owner of tenant
Body:
  { "email": "bob@acme.io", "role": "editor" }
Response:
  201 -> { "membership_id": "uuid", "role": "editor" }
  403 -> insufficient role
```

```
# All existing routes gain tenant-scoping. Tenant is resolved from token + header.
GET /api/workflows
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Tenant: acme
Response:
  200 -> { "workflows": [...] }   # filtered by tenant_id
  403 -> not a member of tenant
```

```
WebSocket /ws/workflow/{workflow_ref}
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Tenant: acme
Server rejects with 1008 if workflow_ref does not belong to acme; run events are scoped to the active tenant and run id.
```

```
POST /hooks/{tenant_slug}/{trigger_id}
# Public endpoint; tenancy resolved from path segment.
Response:
  202 -> { "run_id": "uuid" }
  404 -> unknown tenant_slug or trigger_id
```

## Data Models / Schemas

### `tenants`

| Field | Type | Description |
|-------|------|-------------|
| id | uuid (PK) | Tenant identifier |
| slug | text unique | URL-safe identifier |
| name | text | Display name |
| status | text | `active`, `suspended`, `deleted` |
| quotas | jsonb | Per-tenant quota overrides |
| created_at | timestamptz | Creation time |
| updated_at | timestamptz | Last update |

### `tenant_memberships`

| Field | Type | Description |
|-------|------|-------------|
| id | uuid (PK) | Membership identifier |
| tenant_id | uuid (FK tenants.id) | Tenant |
| user_id | uuid | Principal |
| role | text | `owner`, `admin`, `editor`, `viewer` |
| created_at | timestamptz | Creation time |

Composite unique index `(tenant_id, user_id)`.

### Tenant column on existing tables

Tenant-owned records must be scoped by tenant. PostgreSQL can use `UUID REFERENCES tenants(id)` where the table already uses UUID-style identifiers; SQLite and current text-id tables can store it as `TEXT` with repository-level referential checks. Add a required direct `tenant_id` column to:
`workflows`, `workflow_versions`, `workflow_runs`, `execution_history`, `service_tokens`, `service_token_audit_log`, `credentials`, `credential_templates`, `governance_alerts`, `chat_threads`, `agentensor_checkpoints`, `plugin_installations`, `listener_subscriptions`, `webhook_triggers`, `cron_triggers`, `retry_policies`, `tenant_audit_events`, and tenant-owned LangGraph checkpoint/store records.

`execution_history_steps`, `chat_messages`, `chat_attachments`, `listener_cursors`, and `listener_dedupe` can either carry `tenant_id` directly or enforce tenancy through composite foreign keys to their parent records. Direct `tenant_id` columns are preferred where they simplify hot-path filtering or deletion.

Composite indexes:
- `(tenant_id, handle)` on `workflows`.
- `(tenant_id, updated_at desc)` on `workflows`, `workflow_runs`, `chat_threads`.
- `(tenant_id, workflow_id, version)` unique on `workflow_versions`.
- `(tenant_id, workflow_id, config_version)` on `agentensor_checkpoints`.
- `(tenant_id, lower(name))` unique on `credentials`.
- `(tenant_id, identifier)` and `(tenant_id, secret_hash)` on `service_tokens`.

### `tenant_audit_events`

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "actor_user_id": "uuid",
  "action": "vault.read | membership.add | tenant.suspend | ...",
  "target": { "type": "string", "id": "string" },
  "metadata": { "...": "jsonb" },
  "created_at": "timestamptz"
}
```

### `TenantContext` (in-process)

```json
{
  "tenant_id": "uuid",
  "tenant_slug": "string",
  "user_id": "uuid",
  "role": "owner | admin | editor | viewer",
  "quotas": {
    "max_workflows": 100,
    "max_concurrent_runs": 25,
    "max_credentials": 200,
    "max_storage_rows": 1000000
  }
}
```

## Security Considerations

- Every protected route requires `TenantContext`; absence is a 401.
- Tenant resolution rejects principals without a membership in the requested tenant (403).
- Service tokens are bound to a single tenant at issuance; mismatch is a 401.
- WebSocket and Celery paths re-validate `tenant_id` rather than trusting client claims.
- Public webhook URLs include `tenant_slug` so misrouted events fail closed.
- Vault reads are gated by tenant role (`editor` or higher); reads emit audit events.
- Cross-tenant access attempts are logged with actor and target tenant for forensics.
- Default-tenant migration runs before a second tenant exists; behavioral rollback is only supported in that window.
- Super-admin (deployment-level) operations require a separate role; super-admin tokens never carry `tenant_id`.

## Performance Considerations

- Composite indexes lead with `tenant_id` to keep per-tenant scans selective.
- `tenant_resolver` caches membership in Redis for 60s; invalidation on membership change.
- Per-tenant quota counters live in Redis (`tenant:{id}:concurrent_runs`) with TTL fallback to DB recount.
- Postgres partitioning on `workflow_runs` and `execution_history` by `tenant_id` is reserved for tenants exceeding a threshold (P2); v1 uses index-only.
- Expected overhead per request: <5 ms (one Redis lookup + one extra predicate).
- WebSocket subscriptions are bucketed by tenant to bound fan-out.

## Testing Strategy

- **Unit tests**:
  - `tenant_resolver` cache and invalidation.
  - `TenantContext` propagation through FastAPI dependencies and Celery headers.
  - Repository protocols require `tenant_id` on tenant-owned operations, with targeted tests for each implementation.
- **Integration tests**:
  - For every stateful subsystem, a "cross-tenant isolation" test creates two tenants, writes data in one, and asserts the other cannot read, list, update, or delete it.
  - Service token bound to tenant A cannot access tenant B.
  - Celery task lacking `tenant_id` is rejected by the worker.
  - Public webhook with wrong slug returns 404.
- **Migration tests**:
  - Backfill assigns existing rows to the `default` tenant.
  - Feature-flag rollback preserves default-tenant behavior when no non-default tenant exists.
- **Manual QA checklist**:
  - Provision two tenants; confirm Canvas shows only the active tenant's workflows.
  - Run a workflow in each tenant concurrently; confirm logs/metrics tagged correctly.
  - Exceed a per-tenant quota; confirm graceful rejection.

## Rollout Plan

1. **Phase 1 — Foundation (flag off):** ship tenant tables, `TenantContext`, default-tenant backfill, and column additions. Behavior identical to today.
2. **Phase 2 — Persistence (flag off):** migrate every stateful subsystem to require `tenant_id`. Every route still resolves to `default`.
3. **Phase 3 — Governance (flag toggleable):** role hardening, quotas, audit log, telemetry tagging. Operators can enable the flag in staging.
4. **Phase 4 — GA:** flag on by default for new deployments; existing deployments opt in after verification. Document upgrade and rollback steps.

Backwards compatibility:
- With `multi_tenancy.enabled=false`, all routes resolve to the `default` tenant and the existing CLI/API surface is unchanged.
- Schema changes are treated as forward-only for v1; the feature flag provides behavioral rollback while the `default` tenant remains a permanent fixture.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Claude (Opus 4.7) | Initial draft |
| 2026-05-03 | Codex | Aligned module paths, table names, route examples, rollback language, and persistence scope with the current repository |
