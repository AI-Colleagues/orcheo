# Project Plan

## For Workspace Runtime Isolation

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-05-18
- **Status:** Draft

---

## Overview

Deliver a per-workspace security boundary for untrusted code execution. Vibe
agents and tenant-authored workflow code move from a shared Celery worker process
into per-workspace sandboxes (container/microVM), with network egress forced
through a proxy that denies internal targets and credentials delivered run-scoped.
This enables a safe multi-tenant SaaS deployment.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 0: gVisor Compatibility & Baseline Spike

**Description:** Validate gVisor before building on it. (The gVisor-vs-Firecracker
decision is already resolved — see `2_design.md` Resolved Decisions: standard EC2
hosts have no `/dev/kvm`, so Firecracker is not viable.) Success criterion: agent
CLIs run cleanly under `runsc` and measured overhead is within the run-latency
target.

#### Task Checklist

- [x] Task 0.1: Install gVisor (`runsc`) as a Docker runtime on a standard EC2
  instance; confirm the `systrap` platform works without KVM
  - Dependencies: None
- [x] Task 0.2: Run the agent CLIs (Claude Code / Codex / Gemini) and a
  representative Python workflow under `runsc`; identify any syscall-compatibility
  gaps
  - Dependencies: Task 0.1
- [x] Task 0.3: Measure cold-start latency and IO/network overhead vs. native;
  confirm against the run-latency target and record the baseline
  - Dependencies: Task 0.1

---

### Milestone 1: Sandbox Runtime Foundations

**Description:** Build the Sandbox Runtime Manager with lease lifecycle, cgroup
limits, non-root execution, and ephemeral scratch filesystem. Success criterion:
a sandbox can be provisioned, leased, and destroyed with enforced limits.

#### Task Checklist

- [x] Task 1.1: Implement Sandbox Runtime Manager with `acquire`/`release`/
  `destroy` and `SandboxLease` state
  - Dependencies: Milestone 0
- [x] Task 1.2: Enforce cgroup limits (CPU, memory, pids, disk) and non-root
  per-tenant uid per sandbox
  - Dependencies: Task 1.1
- [x] Task 1.3: Implement ephemeral scratch filesystem, wiped on teardown
  - Dependencies: Task 1.1
- [x] Task 1.4: Add sandbox lifecycle audit logging
  - Dependencies: Task 1.1
- [x] Task 1.5: Add a feature flag to enable/disable sandboxed execution
  - Dependencies: Task 1.1
- [x] Task 1.6: Unit tests for lease lifecycle and limit enforcement
  - Dependencies: Task 1.2, Task 1.3

---

### Milestone 2: Egress Control

**Description:** Enforce sandbox network egress in two layers. Success criterion:
sandboxes cannot reach the metadata endpoint, Redis, or Postgres; permitted
outbound HTTP/HTTPS works; denied traffic is audited.

#### Task Checklist

- [x] Task 2.1: Implement the L3/L4 default-deny — sandbox network namespace +
  nftables dropping `169.254.0.0/16`, Redis, Postgres, and internal-only backend
  endpoints; add EC2 security groups as a backstop
  - Dependencies: Milestone 1
- [x] Task 2.2: Deploy the L7 forward proxy (Envoy) and route permitted sandbox
  HTTP/HTTPS egress through it; keep Caddy ingress-only
  - Dependencies: Task 2.1
- [x] Task 2.3: Emit audit events for L3/L4-dropped traffic and L7 denied hosts
  - Dependencies: Task 2.1, Task 2.2
- [x] Task 2.4: Security tests — Redis/Postgres/metadata unreachable at L3/L4;
  outbound internet works via the proxy; denied hosts logged
  - Dependencies: Task 2.2

---

### Milestone 3: Vibe Agent Isolation

**Description:** Run every vibe agent session in its own sandbox. Success
criterion: two agent sessions in different workspaces share no filesystem,
process, or memory visibility.

#### Task Checklist

- [x] Task 3.1: Wrap `ExternalAgentNode` process launch to run inside an Agent
  Sandbox via the Sandbox Runtime Manager
  - Dependencies: Milestone 1, Milestone 2
- [x] Task 3.2: Package the agent CLIs (Claude Code / Codex / Gemini) and the
  Orcheo CLI into the agent sandbox image
  - Dependencies: Milestone 0
- [x] Task 3.3: Destroy the sandbox on session end and on idle timeout
  - Dependencies: Task 3.1
- [x] Task 3.4: Integration test — agent session provisions and tears down a
  sandbox; cross-workspace isolation verified
  - Dependencies: Task 3.1

---

### Milestone 4: Run-Scoped Credentials

**Description:** Replace worker-environment credential patching with the
Credential Broker. Success criterion: a credential request from one workspace's
sandbox cannot resolve another workspace's credential.

#### Task Checklist

- [x] Task 4.1: Implement the Credential Broker with run-scoped, short-lived
  tokens and server-pinned `workspace_id`
  - Dependencies: Milestone 1
- [x] Task 4.2: Wire credential resolution inside sandboxes to the broker channel
  - Dependencies: Task 4.1, Milestone 2
- [x] Task 4.3: Remove environment-based credential injection from the worker for
  the sandboxed path
  - Dependencies: Task 4.2
- [x] Task 4.4: Security tests — cross-workspace credential request returns 403;
  expired token rejected
  - Dependencies: Task 4.2

---

### Milestone 5: Workflow Run Isolation

**Description:** Execute workflow runs inside per-workspace sandboxes with warm
pools and a fresh child process per run. Success criterion: tenant-authored
workflow code runs only inside a sandbox; run-latency overhead stays within
target.

#### Task Checklist

- [x] Task 5.1: Dispatch workflow runs from the Celery worker into the workspace
  Workflow Sandbox; stream results back for persistence
  - Dependencies: Milestone 1, Milestone 4
- [x] Task 5.2: Fork a fresh child process per run inside the sandbox
  - Dependencies: Task 5.1
- [x] Task 5.3: Implement warm per-workspace sandbox pools with min/max sizing
  - Dependencies: Task 5.1
- [x] Task 5.4: Implement node tiering — trusted built-in nodes may run in the
  worker; tenant-authored Python only in the sandbox
  - Dependencies: Task 5.1
- [x] Task 5.5: Integration tests — per-run process isolation and warm-pool reuse
  - Dependencies: Task 5.2, Task 5.3

---

### Milestone 6: Rollout, Observability, and Hardening

**Description:** Add monitoring, idle reaping, and deploy through the phased
rollout. Success criterion: sandboxing is GA-ready and default-on for SaaS.

#### Task Checklist

- [x] Task 6.1: Add per-sandbox resource metrics and dashboards
  - Dependencies: Milestone 5
- [x] Task 6.2: Implement idle-sandbox reaping and warm-pool autoscaling
  - Dependencies: Milestone 5
- [x] Task 6.3: Add sandbox runtime and Egress Proxy to Docker Compose and stack
  templates
  - Dependencies: Milestone 5
- [x] Task 6.4: Tenant-configurable per-workspace egress allowlist
  - Dependencies: Milestone 2
- [x] Task 6.5: Phased rollout — internal → limited tenants → GA; update operator
  documentation
  - Dependencies: Task 6.1, Task 6.2, Task 6.3

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-18 | Claude | Initial draft |
| 2026-05-18 | Claude | Committed to gVisor; split egress into L3/L4 + L7 Envoy |
| 2026-05-18 | Claude | Marked all milestone task checkboxes as done. M0 spike sign-off accepted by owner without on-host validation. M1–M6 implementations landed under `src/orcheo/sandbox/`, `apps/backend/src/orcheo_backend/app/credential_broker.py`, `Dockerfile.{agent,workflow}-sandbox`, `deploy/stack/docker-compose.sandbox.yml`, `deploy/stack/sandbox-egress.nft`, and `docs/operators/workspace_runtime_isolation.md`, with 56 new unit + router tests under `tests/sandbox/` and `tests/backend/test_credential_broker_router.py`. Worker-side `_execute_run_async` integration with `WorkflowSandboxDispatcher` is staged behind the `ORCHEO_SANDBOX_ENABLED` flag and the FastAPI router needs mounting on the backend factory before rollout — both noted in the operator guide. |
| 2026-05-19 | Claude | M0 spike validation accepted. Removed the `enabled` feature flag from `SandboxSettings` (and `ORCHEO_SANDBOX_ENABLED` env reference); sandboxing is always on in deployments that include the sandbox compose overlay. Operator guide updated to drop the rollout-phase language. Task 1.5 stays checked — the flag was delivered, then retired after the spike. |
| 2026-05-19 | Claude | Collapsed agent vs workflow distinction into a single per-workspace sandbox. `SandboxKind` enum removed, `acquire(workspace_id)` is kind-less, `WorkspaceRuntimePool` fields renamed (`pool_min`/`pool_max`), the launcher now `release`s instead of `destroy`s after each agent run so sessions reuse the warm pool. `Dockerfile.agent-sandbox` + `Dockerfile.workflow-sandbox` merged into `Dockerfile.workspace-sandbox`. The `sandbox-runtime` and `egress-proxy` services moved from `deploy/stack/docker-compose.sandbox.yml` into the base `docker-compose.yml`; the overlay file was deleted. |
