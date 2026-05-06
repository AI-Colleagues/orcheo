# Design Document

## For Multi-workspace support for Orcheo

- **Version:** 0.1
- **Author:** Claude (Opus 4.7), Codex
- **Date:** 2026-05-03
- **Status:** Draft

---

## Overview

This design introduces workspace-scoped identity, persistence, execution, and telemetry to Orcheo so a single deployment can host multiple independent teams or individuals with strict logical isolation. A `WorkspaceContext` is created by auth middleware from a bearer or service token and propagated through FastAPI dependencies, LangGraph state, Celery task headers, and the WebSocket layer. Every repository accepts `workspace_id` as a required argument and every persistence schema gains a `workspace_id` column with composite indexes on hot paths.

The design favors logical isolation in shared databases over physical separation. This keeps the operational surface small (one Postgres, one Redis, one worker pool), preserves Orcheo's existing protocol/factory patterns, and ships behind a single config flag with a backwards-compatible upgrade path that assigns existing data to a `default` workspace.

## Components

- **Workspace core (`orcheo.workspace`)**
  - `Workspace`, `WorkspaceMembership`, `Role` models.
  - `WorkspaceContext` value object (`workspace_id`, `user_id`, `role`, `quotas`).
  - `workspace_resolver` service: resolves principals to memberships, caches in Redis (TTL 60s).
  - Centralized `require_workspace()` FastAPI dependency.
  - SaaS onboarding is invite- or signup-driven: users can switch among memberships they already hold, but they do not get a free-form "join/create workspace" action in the product shell.

- **Identity & Auth (`orcheo_backend.app.authentication`)**
  - Updates bearer token middleware to attach a `WorkspaceContext` per request.
  - Service tokens carry `workspace_id` at issuance; validation rejects token-workspace mismatch.
  - WebSocket handshake requires a workspace-scoped token.
  - Registration and invite acceptance set the initial workspace; the active workspace is then switchable only among memberships.

- **Workflow Repository (`orcheo_backend.app.repository`)**
  - `WorkflowRepository` methods gain an explicit `workspace_id` argument.
  - SQL queries filter by `workspace_id`; composite index `(workspace_id, handle)` and `(workspace_id, updated_at)`.
  - Helper `workspace_scoped(query, workspace_id)` enforces the predicate.

- **Execution History Store (`orcheo_backend.app.history`)**
  - Adds `workspace_id` to `execution_history` and workspace-checks `execution_history_steps` through the parent execution.
  - Read APIs require `workspace_id`; cross-workspace lookups return 404.

- **Service Tokens (`orcheo_backend.app.service_token_repository`)**
  - Tokens are issued for a single `workspace_id`; the column is `NOT NULL`.
  - Rotation and revocation operate within the issuing workspace only.

- **Vault (`orcheo.vault`)**
  - Credential keys become `(workspace_id, name)`; `[[credential_name]]` placeholders resolve in the active workspace only.
  - Per-workspace encryption key derivation (P2: BYO-KMS).

- **ChatKit Store (`orcheo_backend.app.chatkit_store_sqlite`, `orcheo_backend.app.chatkit_store_postgres`)**
  - Threads, messages, and attachments gain `workspace_id`; subscriptions are workspace-scoped.

- **Agentensor Checkpoints (`orcheo_backend.app.agentensor.checkpoint_store`)**
  - Checkpoints gain `workspace_id`; hot lookups use `(workspace_id, workflow_id, config_version)`.

- **Plugins (`orcheo.plugins`)**
  - Plugin install/enable state is per-workspace; allowlist enforced per workspace (P2 expansion).

- **Listeners & Triggers (`orcheo.listeners`, `orcheo.triggers`)**
  - Listener registrations, webhook endpoints, and cron triggers carry `workspace_id`.
  - Public webhook URLs include the workspace slug to avoid ambiguity (`/hooks/{workspace_slug}/{trigger_id}`).

- **Execution Worker (`orcheo_backend.worker`)**
  - Task envelopes carry `workspace_id` in headers; worker rejects unscoped tasks.
  - LangGraph state inherits `workspace_id`; node `decode_variables()` resolves variables in workspace scope.

- **LangGraph Persistence (`orcheo.persistence`)**
  - Checkpointer and graph-store namespaces include `workspace_id`; workspace-owned keys are never shared across workspaces.

- **Telemetry (`orcheo.observability`, `orcheo.telemetry`, `orcheo.tracing`)**
  - OTEL resource attribute `orcheo.workspace`; metrics, logs, and spans tagged with `workspace_id`.
  - Audit log table `workspace_audit_events` for sensitive actions.

- **CLI (`packages/sdk/src/orcheo_sdk/cli`)**
  - `orcheo workspace create|list|deactivate`, `orcheo workspace invite`, `orcheo workspace use <slug>`.
  - All resource commands accept `--workspace <slug>` and read `ORCHEO_WORKSPACE` env var.
  - CLI examples should always make the target workspace explicit; no command should rely on hidden workspace selection when multiple memberships exist.

- **Orcheo Vibe (`orcheo_backend.app.chatkit`, `orcheo.external_agents`)**
  - Each workspace binds its own external-agent credentials, login sessions, and runtime state.
  - Working directories must resolve inside the active workspace's connected filesystem root; the default Vibe path is workspace-scoped, not shared across tenants.
  - External CLI agents only receive workspace-scoped auth material and workspace-scoped path access.

## Request Flows

### Flow 1: Authenticated API request
1. Client sends `Authorization: Bearer <token>` and optional `X-Orcheo-Workspace: <slug>`.
2. Auth middleware validates the token and looks up principal memberships via `workspace_resolver`.
3. Middleware selects the workspace (from token claim, `X-Orcheo-Workspace` header, or principal's default).
4. `WorkspaceContext` is attached to `request.state`; downstream `require_workspace()` dependency exposes it.
5. Route handler calls repositories with `workspace_id`; queries filter by it.

### Flow 2: Workflow execution
1. API receives `POST /api/workflows/{workflow_ref}/runs` with `workspace_id` from `WorkspaceContext`.
2. Run is persisted with `workspace_id` and dispatched to Celery with `workspace_id` in task headers.
3. Worker rebuilds `WorkspaceContext`, hydrates LangGraph state including `workspace_id`.
4. Nodes resolve variables and credentials via workspace-scoped vault.
5. Execution-history events stream via WebSocket scoped to `(workspace_id, run_id)`.

### Flow 3: Listener / webhook delivery
1. External service POSTs to `/hooks/{workspace_slug}/{trigger_id}`.
2. Trigger router resolves the slug to `workspace_id`; rejects unknown slugs.
3. Trigger handler enqueues a run in the resolved workspace; Celery task carries `workspace_id`.

### Flow 4: Workspace provisioning
1. Operator runs `orcheo workspace create --slug acme --owner-email alice@acme.io`.
2. CLI calls admin API with deployment admin token (super-admin scope).
3. Admin API creates the workspace row, default quotas, and an `owner` membership for the named user.
4. CLI prints the new workspace slug and an initial bootstrap service token.

### Flow 5: SaaS registration / invite acceptance
1. New users register or accept an invite.
2. The identity layer binds the account to the invited workspace or provisions the first workspace only if the deployment policy allows self-serve creation.
3. Canvas opens in that workspace with the active workspace switcher set to one of the user's existing memberships.
4. Users can switch workspaces only within that membership set; they cannot browse the entire tenant graph to join or create arbitrary workspaces.

### Flow 6: Single-workspace upgrade
1. Operator deploys release with `multi_workspace.enabled=false` and `multi_workspace.default_workspace_slug=default`.
2. Schema migration adds nullable `workspace_id` columns, backfills with the `default` workspace id, then sets `NOT NULL`.
3. After verification, operator flips `multi_workspace.enabled=true`.
4. Subsequent requests must resolve to a workspace; absent header defaults to the principal's primary membership.

## API Contracts

```
POST /api/admin/workspaces
Headers:
  Authorization: Bearer <super-admin-token>
Body:
  { "slug": "acme", "name": "Acme Inc", "owner_email": "alice@acme.io" }
Response:
  201 -> { "workspace_id": "uuid", "slug": "acme", "bootstrap_token": "<service-token>" }
  409 -> slug conflict
```

```
GET /api/workspaces/me
Headers:
  Authorization: Bearer <user-token>
Response:
  200 -> { "memberships": [{ "workspace_id": "uuid", "slug": "acme", "role": "editor" }, ...] }
```

```
POST /api/workspaces/{slug}/members
Headers:
  Authorization: Bearer <user-token>   # must be admin/owner of workspace
Body:
  { "email": "bob@acme.io", "role": "editor" }
Response:
  201 -> { "membership_id": "uuid", "role": "editor" }
  403 -> insufficient role
```

```
# All existing routes gain workspace-scoping. Workspace is resolved from token + header.
GET /api/workflows
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: acme
Response:
  200 -> { "workflows": [...] }   # filtered by workspace_id
  403 -> not a member of workspace
```

```
WebSocket /ws/workflow/{workflow_ref}
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: acme
Server rejects with 1008 if workflow_ref does not belong to acme; run events are scoped to the active workspace and run id.
```

```
POST /hooks/{workspace_slug}/{trigger_id}
# Public endpoint; workspace resolved from path segment.
Response:
  202 -> { "run_id": "uuid" }
  404 -> unknown workspace_slug or trigger_id
```

## Data Models / Schemas

### `workspaces`

| Field | Type | Description |
|-------|------|-------------|
| id | uuid (PK) | Workspace identifier |
| slug | text unique | URL-safe identifier |
| name | text | Display name |
| status | text | `active`, `suspended`, `deleted` |
| quotas | jsonb | Per-workspace quota overrides |
| created_at | timestamptz | Creation time |
| updated_at | timestamptz | Last update |

### `workspace_memberships`

| Field | Type | Description |
|-------|------|-------------|
| id | uuid (PK) | Membership identifier |
| workspace_id | uuid (FK workspaces.id) | Workspace |
| user_id | uuid | Principal |
| role | text | `owner`, `admin`, `editor`, `viewer` |
| created_at | timestamptz | Creation time |

Composite unique index `(workspace_id, user_id)`.

### Workspace column on existing tables

Workspace-owned records must be scoped by workspace. PostgreSQL can use `UUID REFERENCES workspaces(id)` where the table already uses UUID-style identifiers; SQLite and current text-id tables can store it as `TEXT` with repository-level referential checks. Add a required direct `workspace_id` column to:
`workflows`, `workflow_versions`, `workflow_runs`, `execution_history`, `service_tokens`, `service_token_audit_log`, `credentials`, `credential_templates`, `governance_alerts`, `chat_threads`, `agentensor_checkpoints`, `plugin_installations`, `listener_subscriptions`, `webhook_triggers`, `cron_triggers`, `retry_policies`, `workspace_audit_events`, and workspace-owned LangGraph checkpoint/store records.

`execution_history_steps`, `chat_messages`, `chat_attachments`, `listener_cursors`, and `listener_dedupe` can either carry `workspace_id` directly or enforce workspace through composite foreign keys to their parent records. Direct `workspace_id` columns are preferred where they simplify hot-path filtering or deletion.

Composite indexes:
- `(workspace_id, handle)` on `workflows`.
- `(workspace_id, updated_at desc)` on `workflows`, `workflow_runs`, `chat_threads`.
- `(workspace_id, workflow_id, version)` unique on `workflow_versions`.
- `(workspace_id, workflow_id, config_version)` on `agentensor_checkpoints`.
- `(workspace_id, lower(name))` unique on `credentials`.
- `(workspace_id, identifier)` and `(workspace_id, secret_hash)` on `service_tokens`.

### `workspace_audit_events`

```json
{
  "id": "uuid",
  "workspace_id": "uuid",
  "actor_user_id": "uuid",
  "action": "vault.read | membership.add | workspace.suspend | ...",
  "target": { "type": "string", "id": "string" },
  "metadata": { "...": "jsonb" },
  "created_at": "timestamptz"
}
```

### `WorkspaceContext` (in-process)

```json
{
  "workspace_id": "uuid",
  "workspace_slug": "string",
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

- Every protected route requires `WorkspaceContext`; absence is a 401.
- Workspace resolution rejects principals without a membership in the requested workspace (403).
- Service tokens are bound to a single workspace at issuance; mismatch is a 401.
- WebSocket and Celery paths re-validate `workspace_id` rather than trusting client claims.
- Public webhook URLs include `workspace_slug` so misrouted events fail closed.
- Vault reads are gated by workspace role (`editor` or higher); reads emit audit events.
- Cross-workspace access attempts are logged with actor and target workspace for forensics.
- Default-workspace migration runs before a second workspace exists; behavioral rollback is only supported in that window.
- Super-admin (deployment-level) operations require a separate role; super-admin tokens never carry `workspace_id`.
- Orcheo Vibe requests must refuse to execute if the selected working directory falls outside the workspace's connected filesystem root.

## Performance Considerations

- Composite indexes lead with `workspace_id` to keep per-workspace scans selective.
- `workspace_resolver` caches membership in Redis for 60s; invalidation on membership change.
- Per-workspace quota counters live in Redis (`workspace:{id}:concurrent_runs`) with TTL fallback to DB recount.
- Postgres partitioning on `workflow_runs` and `execution_history` by `workspace_id` is reserved for workspaces exceeding a threshold (P2); v1 uses index-only.
- Expected overhead per request: <5 ms (one Redis lookup + one extra predicate).
- WebSocket subscriptions are bucketed by workspace to bound fan-out.

## Testing Strategy

- **Unit tests**:
  - `workspace_resolver` cache and invalidation.
  - `WorkspaceContext` propagation through FastAPI dependencies and Celery headers.
  - Repository protocols require `workspace_id` on workspace-owned operations, with targeted tests for each implementation.
- **Integration tests**:
  - For every stateful subsystem, a "cross-workspace isolation" test creates two workspaces, writes data in one, and asserts the other cannot read, list, update, or delete it.
  - Service token bound to workspace A cannot access workspace B.
  - Celery task lacking `workspace_id` is rejected by the worker.
  - Public webhook with wrong slug returns 404.
- **Migration tests**:
  - Backfill assigns existing rows to the `default` workspace.
  - Feature-flag rollback preserves default-workspace behavior when no non-default workspace exists.
- **Manual QA checklist**:
  - Provision two workspaces; confirm Canvas shows only the active workspace's workflows.
  - Run a workflow in each workspace concurrently; confirm logs/metrics tagged correctly.
  - Exceed a per-workspace quota; confirm graceful rejection.

## Rollout Plan

1. **Phase 1 — Foundation (flag off):** ship workspace tables, `WorkspaceContext`, default-workspace backfill, and column additions. Behavior identical to today.
2. **Phase 2 — Persistence (flag off):** migrate every stateful subsystem to require `workspace_id`. Every route still resolves to `default`.
3. **Phase 3 — Governance (flag toggleable):** role hardening, quotas, audit log, telemetry tagging. Operators can enable the flag in staging.
4. **Phase 4 — GA:** flag on by default for new deployments; existing deployments opt in after verification. Document upgrade and rollback steps.

Backwards compatibility:
- With `multi_workspace.enabled=false`, all routes resolve to the `default` workspace and the existing CLI/API surface is unchanged.
- Schema changes are treated as forward-only for v1; the feature flag provides behavioral rollback while the `default` workspace remains a permanent fixture.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-03 | Claude (Opus 4.7) | Initial draft |
| 2026-05-03 | Codex | Aligned module paths, table names, route examples, rollback language, and persistence scope with the current repository |
