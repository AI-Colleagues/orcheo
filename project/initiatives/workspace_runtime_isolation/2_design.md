# Design Document

## For Workspace Runtime Isolation

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-05-18
- **Status:** Draft

---

## Overview

This design gives each workspace a real security boundary for the untrusted code
it runs. Today Orcheo isolates workspaces logically — every table carries a
`workspace_id` and queries filter on it — but vibe agents and tenant-authored
workflow code execute in a shared Celery worker process, on a shared kernel, with
a shared network namespace and filesystem. For a multi-tenant SaaS that is not a
sufficient boundary: a hostile or buggy workspace can read another workspace's
secrets, files, or memory, and can reach internal services such as the cloud
metadata endpoint, Redis, and Postgres.

The design introduces a **Sandbox Runtime Manager** that runs vibe agent sessions
and workflow code inside per-workspace **gVisor sandboxes** (`runsc` as a Docker
runtime). gVisor is chosen because the target deployment is standard EC2
instances running docker-compose, which do not expose `/dev/kvm`; a Firecracker
microVM would require bare-metal `*.metal` hosts, whereas gVisor's `systrap`
platform needs no KVM. Sandbox network egress is controlled in two layers — an
L3/L4 default-deny for internal targets and an L7 forward proxy for permitted
HTTP/HTTPS — and credentials are delivered **run-scoped** by a **Credential
Broker** rather than patched into the worker's environment.

A key distinction drives the design: a fresh process or a dedicated Linux uid
provides *fault isolation* and filesystem DAC, but not *security isolation*
against hostile code. Only namespaces + cgroups + seccomp (a container) or a
microVM provide that. Vibe agents need outbound network access, so a pure
filesystem/uid approach cannot contain them — the sandbox boundary is required.
Logical `workspace_id` isolation is retained underneath as defense-in-depth.

## Components

- **Sandbox Runtime Manager (Backend / Python)**
  - Responsibility: Provision, lease, track, and destroy sandboxes; own the warm
    per-workspace pools; enforce cgroup limits and non-root execution.
  - Key interfaces: `acquire(workspace_id, kind) -> SandboxLease`,
    `release(lease)`, `destroy(lease)`. `kind` ∈ {`agent`, `workflow`}.
  - Dependencies: host container runtime (see note below), L3/L4 egress policy,
    L7 forward proxy, Credential Broker.

  > **Container-runtime access:** the Manager spawns `runsc` containers at
  > runtime, so it needs a client connection to the host container runtime — the
  > Docker socket (`/var/run/docker.sock`) or a containerd socket. That socket is
  > root-equivalent on the host: any process that can reach it can escape to the
  > host. The Manager therefore runs as its own minimal dedicated service — not
  > inside the multi-purpose worker container — exposes only the
  > `acquire`/`release`/`destroy` API to the worker and backend, and never grants
  > sandbox or tenant code any path to the socket. Prefer a rootless or
  > API-proxied containerd setup over mounting the raw Docker socket.

- **Agent Sandbox (gVisor container)**
  - Responsibility: Host one vibe agent session — the agent CLI (Claude Code /
    Codex / Gemini) plus the Orcheo CLI — on an ephemeral filesystem as a
    non-root, per-tenant uid.
  - Dependencies: Sandbox Runtime Manager (lifecycle), Egress Proxy (all network).

- **Workflow Sandbox (gVisor container)**
  - Responsibility: Execute workflow runs for a single workspace; each run runs
    in a fresh child process. Warm-pooled to amortize startup.
  - Dependencies: Sandbox Runtime Manager, Egress Proxy, Credential Broker.

- **Egress Network Policy (L3/L4)**
  - Responsibility: The actual network boundary. Each sandbox runs in a network
    namespace whose nftables rules drop all traffic to `169.254.0.0/16` (cloud
    metadata), Redis, Postgres, and internal-only backend endpoints. EC2 security
    groups act as a backstop. Enforcement does not depend on tenant code.
  - Dependencies: None (host networking + nftables).

- **Egress Forward Proxy (Envoy, L7)**
  - Responsibility: Handle permitted outbound HTTP/HTTPS — host allowlisting,
    per-workspace egress allowlists, and denied-host audit logging. Not a
    security boundary on its own; it governs the traffic L3/L4 already permits.
  - Dependencies: None (infrastructure component). Distinct from the existing
    Caddy ingress, which keeps doing inbound TLS/reverse-proxy only.

- **Credential Broker (Backend / Python)**
  - Responsibility: Issue run-scoped, short-lived credential material to a
    sandbox over an authenticated channel. Pins the workspace context so tenant
    code cannot spoof `X-Orcheo-Workspace`.
  - Dependencies: Vault (`src/orcheo/vault/`).

- **Execution Worker (Celery, modified)**
  - Responsibility: Instead of executing workflows in-process, dispatch each run
    into the workspace's Workflow Sandbox and stream results back for persistence.
  - Dependencies: Sandbox Runtime Manager, repository layer.

## Request Flows

### Flow 1: Vibe agent session

1. Tenant starts an agent session via Canvas/API; backend authenticates and
   resolves the `WorkspaceContext`.
2. Backend asks the Sandbox Runtime Manager to `acquire(workspace_id, "agent")`.
3. Manager provisions an Agent Sandbox: ephemeral filesystem, non-root per-tenant
   uid, cgroup limits, and a network namespace with the L3/L4 default-deny policy
   applied; permitted HTTP/HTTPS is routed through the L7 forward proxy.
4. Credential Broker injects run-scoped credentials / OAuth tokens for that
   workspace into the sandbox over the authenticated channel.
5. The agent CLI runs inside the sandbox. Orcheo CLI calls reach the backend's
   authenticated public API through the L7 forward proxy with the workspace
   context pinned by the broker — the tenant cannot override it. Internal
   hostnames (Redis, Postgres, `backend:2025`) remain unreachable.
6. On session end or idle timeout, the Manager destroys the sandbox and wipes its
   scratch filesystem.

### Flow 2: Workflow run

1. A webhook or cron trigger creates a pending run (unchanged from today).
2. The Celery worker picks up the run and resolves its `workspace_id`.
3. Worker calls `acquire(workspace_id, "workflow")`; the Manager returns a warm
   Workflow Sandbox from the workspace pool, or cold-provisions one.
4. Worker dispatches the run into the sandbox; the sandbox forks a **fresh child
   process** for this run (fault isolation between runs of the same workspace).
5. The Credential Broker supplies run-scoped credentials for the run.
6. Tenant-authored node code executes in the child process. Trusted built-in
   nodes may be fast-pathed (see Node Tiering below).
7. The child process exits; results stream back to the worker and are persisted.
   The sandbox is released back to the warm pool.

### Flow 3: Blocked egress attempt

1. Code inside a sandbox attempts to reach `169.254.169.254`, Redis, or Postgres.
2. The L3/L4 nftables policy drops the packet — non-HTTP targets never leave the
   namespace. A disallowed HTTP host is instead rejected by the L7 forward proxy.
3. An audit event is emitted with workspace, run, and destination.

## API Contracts

No new public HTTP APIs. Two internal contracts are introduced.

**Sandbox Runtime Manager (internal, in-process or local RPC):**

```
acquire(workspace_id: str, kind: "agent" | "workflow") -> SandboxLease
release(lease: SandboxLease) -> None        # return workflow sandbox to pool
destroy(lease: SandboxLease) -> None        # tear down (always for agent kind)
```

**Credential Broker (internal channel from sandbox to backend):**

```
POST /internal/credentials/resolve
Headers:
  Authorization: Bearer <broker-issued, run-scoped token>
Body:
  { "run_id": "uuid", "credential_name": "string" }

Response:
  200 OK -> { "value": "string", "expires_at": "datetime" }
  403    -> credential not in this run's workspace scope
  401    -> invalid or expired run-scoped token
```

The run-scoped token encodes `workspace_id` and `run_id` server-side; tenant code
cannot mint or alter it.

## Data Models / Schemas

**SandboxLease** (in-memory / Manager state):

| Field | Type | Description |
|-------|------|-------------|
| lease_id | string | Unique lease identifier |
| workspace_id | string | Owning workspace |
| kind | string | `agent` or `workflow` |
| sandbox_id | string | Underlying container/microVM id |
| state | string | `provisioning` → `ready` → `in_use` → `released`/`destroyed` |
| created_at | datetime | Provision time |
| last_used_at | datetime | For idle reaping |

**WorkspaceRuntimePool** (configuration):

```json
{
  "workspace_id": "string",
  "workflow_pool_min": 1,
  "workflow_pool_max": 4,
  "cpu_limit": "string",
  "memory_limit": "string",
  "pid_limit": 256,
  "scratch_disk_limit": "string",
  "idle_ttl_seconds": 900
}
```

**Sandbox audit log entry** (persisted):

| Field | Type | Description |
|-------|------|-------------|
| event | string | `provision`/`destroy`/`egress_denied`/`oom`/`timeout` |
| workspace_id | string | Owning workspace |
| sandbox_id | string | Sandbox identifier |
| run_id | string \| null | Associated run, if any |
| detail | string | Destination, limit hit, etc. |
| created_at | datetime | Event time |

## Security Considerations

- **Threat model:** vibe agents and tenant-authored workflow code are *untrusted*.
  Assume the tenant is hostile and may attempt to read co-tenant data, escape the
  sandbox, or reach internal services.
- **Boundary:** namespaces + cgroups + seccomp (container) or a microVM. A fresh
  process or per-tenant uid alone is *not* a tenant boundary — it is retained
  only as defense-in-depth inside the sandbox.
- **Network (two layers):** L3/L4 default-deny is the real boundary — the sandbox
  network namespace + nftables (EC2 security groups as backstop) drop all traffic
  to `169.254.0.0/16` (cloud metadata), Redis, Postgres, and internal-only
  backend endpoints, regardless of whether tenant code cooperates. Permitted
  outbound HTTP/HTTPS flows through the L7 forward proxy for host allowlisting and
  denied-host audit logging.
- **Credentials:** delivered run-scoped and short-lived via the Credential Broker;
  never baked into a sandbox image and never broadly readable. The broker pins
  `workspace_id` server-side so tenant code cannot spoof the workspace header.
- **Filesystem:** ephemeral per-session / per-run scratch space, wiped on
  teardown; no shared writable mounts between workspaces.
- **Privilege:** non-root, per-tenant uid inside every sandbox; cgroup limits on
  CPU, memory, pids, and disk to prevent noisy-neighbor and resource-exhaustion
  DoS.
- **Container-runtime socket:** the Sandbox Runtime Manager needs access to the
  host container runtime (Docker/containerd socket) to spawn `runsc` containers.
  This socket is root-equivalent on the host and must never be reachable from a
  sandbox or from tenant code. Run the Manager as a minimal dedicated service
  isolated from sandbox workloads, and prefer a rootless or API-proxied
  containerd setup over a raw Docker socket mount.
- **Node tiering:** only Orcheo's first-party built-in nodes (AI, integration,
  data transform) may run in the worker process; any tenant-authored Python runs
  exclusively inside a sandbox.
- **Defense-in-depth:** existing logical `workspace_id` filtering and the vault's
  `WorkflowScopeError` checks remain in force.

## Performance Considerations

- Agent sessions are long-lived (minutes); sandbox boot (~hundreds of ms for a
  microVM) is negligible against session length.
- Workflow runs can be short and frequent; per-run cold provisioning would
  dominate latency. Mitigation: **warm per-workspace pools** sized by
  `workflow_pool_min`/`max`, with idle reaping after `idle_ttl_seconds`.
- The L7 forward proxy adds a hop for outbound HTTP/HTTPS; size it for aggregate
  sandbox throughput. L3/L4 filtering is in-kernel and adds negligible overhead.
- gVisor intercepts syscalls in userspace; network- and IO-heavy workloads carry
  measurable overhead. Validate against the run-latency target in Milestone 0.
- Credential Broker calls are on the run hot path — keep resolution fast and
  cache within a run's lifetime only.

## Testing Strategy

- **Unit tests:** Sandbox Runtime Manager lease lifecycle; pool acquire/release;
  Credential Broker scope enforcement (cross-workspace request → 403).
- **Integration tests:** agent session → sandbox provisioned/destroyed; workflow
  run → fresh child process per run; warm-pool reuse across runs.
- **Security tests:** egress to `169.254.169.254`/Redis/Postgres is denied;
  attempt to read another workspace's filesystem/credentials fails; cgroup limits
  enforced (OOM, pid cap); workspace-header spoof from inside a sandbox rejected.
- **Manual QA checklist:** start two agent sessions in different workspaces and
  confirm no cross-visibility; run a deliberately resource-heavy workflow and
  confirm it cannot starve a co-tenant.

## Rollout Plan

1. Phase 1: Sandbox Runtime Manager + agent isolation behind a feature flag;
   internal workspaces only.
2. Phase 2: Workflow-run isolation + warm pools; limited monitored multi-tenant
   rollout.
3. Phase 3: General availability — sandboxing default-on for SaaS; egress
   allowlists and idle reaping enabled. Self-hosted/single-tenant deployments may
   disable sandboxing via configuration.

Feature flag: a single configuration switch enables/disables sandboxed execution;
when disabled, workflow execution falls back to the current in-process path.

## Open Issues

- [ ] Whether trusted built-in-only workflows should skip the sandbox entirely as
  a fast path, or always route through it for uniformity.
- [ ] Warm-pool sizing defaults and autoscaling policy.
- [ ] gVisor IO/network overhead under the forward-proxy path — quantify in the
  Milestone 0 spike and confirm it stays within the run-latency target.

## Resolved Decisions

- **Isolation technology — gVisor (`runsc`).** The target deployment is standard
  EC2 instances running docker-compose, which do not expose `/dev/kvm`;
  Firecracker would require bare-metal `*.metal` hosts. gVisor's `systrap`
  platform needs no KVM and installs as a Docker runtime. Trade-off accepted:
  gVisor's syscall coverage is not 100% (validated in Milestone 0) and its
  boundary, while strong, is not microVM-grade.
- **Egress — L3/L4 network policy + L7 Envoy forward proxy.** An HTTP proxy
  cannot police raw Redis/Postgres connections, and the boundary must not depend
  on tenant code using a proxy. L3/L4 nftables is the boundary; Envoy governs
  permitted HTTP/HTTPS. The existing Caddy stays ingress-only.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-18 | Claude | Initial draft |
| 2026-05-18 | Claude | Resolved isolation tech (gVisor) and egress design (L3/L4 + Envoy) |
| 2026-05-18 | Claude | Added container-runtime socket access note for the Sandbox Runtime Manager |
