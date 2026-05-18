# Requirements Document

## METADATA
- **Authors:** Claude
- **Project/Feature Name:** Workspace Runtime Isolation
- **Type:** Feature
- **Summary:** Give each workspace a real security boundary for the code it runs.
  Vibe agents and tenant-authored workflow code currently execute with a shared
  kernel, network, and filesystem; this initiative moves them into per-workspace
  gVisor sandboxes with controlled network egress and run-scoped
  credentials, so activity in one workspace cannot reach another's data,
  secrets, compute, or internal services.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-05-18

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Prior Artifacts | `../multi_workspace/` | Owner | Shaojie Jiang |
| Prior Artifacts | `../execution_worker/` | Owner | Shaojie Jiang |
| Prior Artifacts | `../external_agent_cli_nodes/` | Owner | Shaojie Jiang |
| Design Review | `./2_design.md` | Author | Claude |
| Eng Requirement Doc | `./1_requirements.md` | Author | Claude |

## PROBLEM DEFINITION
### Objectives
Provide a per-workspace security boundary for untrusted code execution — vibe
agents and tenant-authored workflow code — so a multi-tenant SaaS deployment can
guarantee that one workspace cannot access another's data, secrets, network, or
compute.

### Target users
Platform operators running Orcheo as a multi-tenant SaaS. Indirectly, every
tenant whose workflows, credentials, and agent sessions must remain private from
co-tenants on the same infrastructure.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform operator | run vibe agents from different workspaces on shared hosts | I can offer multi-tenant SaaS without per-tenant infrastructure | P0 | Each agent session runs in its own sandbox; no cross-workspace filesystem, process, or memory access |
| Platform operator | run tenant-authored workflow code safely | a buggy or hostile workflow cannot affect other tenants | P0 | Workflow runs execute inside a per-workspace sandbox; each run gets a fresh child process |
| Security reviewer | block sandboxes from internal services | tenant code cannot reach the cloud metadata endpoint, Redis, or Postgres directly | P0 | Egress to link-local and internal CIDRs is denied by default; outbound internet flows only through the egress proxy |
| Tenant | have my credentials injected only into my own runs | another tenant's code can never read my secrets | P0 | Credentials are delivered run-scoped and short-lived; never baked into a shared image |
| Platform operator | keep workflow latency acceptable | short, frequent runs are not dominated by sandbox startup | P1 | Warm per-workspace sandbox pools amortize startup; run-latency overhead stays within target |
| Operator | observe and reap sandboxes | I can monitor resource use and avoid leaks | P1 | Per-sandbox metrics and lifecycle audit logs exist; idle sandboxes are reaped |

### Context, Problems, Opportunities
Workspace separation in Orcheo today is **logical, not physical**. Every relevant
table carries a `workspace_id` column and queries filter on it; the vault scopes
credentials per workspace; `workspace_governance` enforces per-workspace quotas.

However, the **execution layer is shared**. Vibe agents (`ExternalAgentNode` →
Claude Code / Codex / Gemini CLIs) and workflow code run in a shared Celery
worker process, on a shared kernel, with a shared network namespace and shared
filesystem. Vibe agents execute untrusted code by design — they run whatever the
LLM or tenant decides. LangGraph workflow definitions are also tenant-authored
Python. A malicious or buggy workspace can therefore read another workspace's
environment variables, credentials, files, or process memory, and can reach
internal services directly — most dangerously the cloud metadata endpoint
(`169.254.169.254`), Redis, and Postgres. This is an unacceptable risk for a
multi-tenant SaaS and currently blocks that offering.

Existing per-workspace controls for external agents (per-workspace
`environment.json`, per-workspace auth caches, the `/workspace/agents/{id}`
filesystem root, `start_new_session=True` subprocesses) provide fault isolation
and filesystem DAC, but not a security boundary against hostile code.

### Product goals and Non-goals
**Goals:**
- A container/microVM security boundary per vibe agent session.
- A per-workspace container/microVM boundary for workflow runs, with a fresh
  child process per run for fault isolation.
- Network egress control: default-deny to the link-local metadata range and
  internal services; general outbound internet allowed only via an egress proxy.
- Run-scoped, short-lived credential delivery; secrets never broadly readable
  inside a sandbox beyond the run that needs them.
- Per-sandbox resource limits (CPU, memory, pids, disk) via cgroups.
- Defense-in-depth: non-root, per-tenant uid inside each sandbox.

**Non-goals:**
- Replacing logical (`workspace_id`) isolation — it remains as defense-in-depth.
- Sandboxing Orcheo's own trusted built-in nodes (AI, integration, data
  transform) — they are first-party code and continue to run in-process.
- A fresh microVM for every single workflow run — warm per-workspace pools are
  used instead to control cold-start cost.
- Cross-region or data-residency isolation.
- Hardening the Canvas frontend or the API server itself — this initiative
  covers execution runtimes only.

## PRODUCT DEFINITION
### Requirements
**P0 (blocking multi-tenant SaaS):**
- Sandbox Runtime Manager that provisions, leases, tracks, and destroys isolated
  sandboxes.
- Each vibe agent session runs in its own sandbox, destroyed at session end or
  idle timeout.
- Workflow runs execute inside a per-workspace sandbox; each run runs in a fresh
  child process within that sandbox.
- Network egress default-deny at L3/L4 (sandbox network namespace + nftables,
  with EC2 security groups as backstop) to `169.254.0.0/16`, Redis, Postgres,
  and internal-only backend endpoints. Permitted outbound HTTP/HTTPS flows
  through an L7 forward proxy for host allowlisting and audit logging.
- cgroup limits (CPU, memory, pids, disk/scratch) per sandbox.
- Run-scoped credential injection: credentials are scoped to the workspace and
  the specific run, short-lived, and never persisted in a sandbox image.
- Non-root execution inside every sandbox.

**P1:**
- Warm pool of per-workspace workflow sandboxes to amortize startup latency.
- Node tiering: trusted built-in nodes may run in the worker; tenant-authored
  Python only ever runs inside a sandbox.
- Filesystem confinement: ephemeral per-session / per-run scratch space, wiped on
  teardown.
- Observability: per-sandbox resource metrics and an audit log of sandbox
  lifecycle and denied-egress events.

**P2:**
- Idle-sandbox reaping and autoscaling of warm pools.
- Tenant-configurable per-workspace egress allowlist.
- Tuned seccomp / AppArmor (or equivalent) profiles.

### Designs (if applicable)
N/A — backend/infrastructure initiative; see `./2_design.md`.

### [Optional] Other Teams Impacted
- **Execution Worker:** workflow execution moves from in-process to dispatch into
  a per-workspace sandbox; `tasks.py` execution path changes.
- **External Agent CLI Nodes:** agent process launch is wrapped by the Sandbox
  Runtime Manager rather than a bare subprocess.
- **Vault / Credentials:** credential delivery becomes run-scoped via a broker
  channel instead of environment patching in the worker process.
- **Deployment / Stack:** new infrastructure components (sandbox runtime, egress
  proxy) must be added to Docker Compose and stack templates.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
A new **Sandbox Runtime Manager** owns the lifecycle of isolated execution
environments. Vibe agent sessions each receive a dedicated sandbox; workflow runs
are dispatched into a warm, per-workspace sandbox and forked into a fresh child
process. All sandbox network is default-denied to internal targets at L3/L4,
with permitted outbound HTTP/HTTPS flowing through an L7 forward proxy. A
**Credential Broker** delivers run-scoped
secrets over an authenticated channel so credentials are never baked into images
or broadly readable. Logical `workspace_id` isolation is retained underneath as
defense-in-depth.

### Technical Requirements
- Isolation technology: gVisor (`runsc`) as a Docker runtime. Chosen because the
  target deployment is standard EC2 instances running docker-compose, which do
  not expose `/dev/kvm` — a microVM (Firecracker) would require bare-metal
  `*.metal` hosts. gVisor's `systrap` platform needs no KVM. The Milestone 0
  spike validates agent-CLI syscall compatibility under `runsc`.
- Sandbox startup budget must keep agent-session start and workflow-run overhead
  within target; warm pools required for workflow runs.
- Network egress is enforced in two layers: an L3/L4 default-deny (network
  namespace + nftables, EC2 security groups as backstop) for internal targets,
  and an L7 forward proxy for host allowlisting and denied-request audit logs.
- Credential Broker channel must be authenticated and pin the workspace context
  so tenant code cannot spoof `X-Orcheo-Workspace`.
- Non-root uid, cgroup limits, and ephemeral scratch filesystem per sandbox.
- Graceful degradation: a single-tenant or self-hosted deployment can disable
  sandboxing via configuration without breaking workflow execution.

## LAUNCH/ROLLOUT PLAN
### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Cross-workspace access incidents | 0 — the boundary must hold |
| [Secondary] Agent-session start latency (p95) | Within agreed budget; sandbox boot is negligible against multi-minute sessions |
| [Secondary] Workflow run latency overhead vs. baseline | < agreed % with warm pools |
| [Guardrail] Denied internal-egress attempts | Logged and alerted; no successful reaches to metadata/Redis/Postgres |
| [Guardrail] Sandbox leak rate | ~0 leaked sandboxes after idle reaping |

### Rollout Strategy
Feature-flagged. Internal dogfood deployment first, then a limited set of
multi-tenant workspaces with monitoring, then general availability. Single-tenant
and self-hosted deployments may keep sandboxing off via configuration.

### Estimated Launch Phases (if applicable)
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal | Sandbox runtime + agent isolation behind a flag; internal workspaces only |
| **Phase 2** | Limited tenants | Workflow-run isolation + warm pools; monitored multi-tenant rollout |
| **Phase 3** | GA | Sandboxing default-on for SaaS; egress allowlists and reaping enabled |

## HYPOTHESIS & RISKS
Hypothesis: Moving vibe agents and tenant-authored workflow code into
per-workspace sandboxes with controlled egress establishes a true security
boundary, enabling a safe multi-tenant SaaS offering. Confidence is high — this
is the standard model for untrusted code execution platforms (CI runners, code
sandboxes).

Risks:
- **Cold-start latency** for short, frequent workflow runs. Mitigation: warm
  per-workspace sandbox pools; run latency tracked as a guardrail metric.
- **Operational complexity** of a new runtime and egress proxy. Mitigation: a
  Milestone 0 spike to pick the lowest-operational-cost technology that still
  provides a kernel boundary; configuration to disable for self-hosted.
- **Incomplete egress policy** leaving an internal service reachable. Mitigation:
  default-deny posture, audited denied requests, and a review of all internal
  CIDRs before GA.
- **Credential exposure** if the broker channel is weak. Mitigation: authenticated
  channel, short-lived run-scoped material, workspace context pinned server-side.

## APPENDIX
**Isolation vocabulary used in this initiative:**
- *Fault isolation* — fresh process / uid / filesystem DAC. Prevents a crash or
  bug from affecting siblings. Necessary but not a security boundary.
- *Security isolation* — namespaces + cgroups + seccomp (container) or a microVM.
  Required for untrusted, multi-tenant code.
