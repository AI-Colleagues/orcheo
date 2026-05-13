# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** External Agent CLI Nodes — Claude Code and Codex as Workflow Nodes
- **Type:** Feature
- **Summary:** Add first-class Orcheo workflow nodes that invoke the actual Claude Code and Codex CLIs from the execution worker. V1 focuses on a CLI-first integration with minimal configuration, latest-channel installs with scheduled maintenance checks, clear manual login guidance, and a shared generic runtime layer for future external agent providers.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-31

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Browser Context Initiative | `project/initiatives/browser_context/1_requirements.md` | ShaojieJiang | Agent-to-Orcheo context bridge |
| Deep Agent Skill Management | `project/initiatives/deep_agent_skill_management/1_requirements.md` | ShaojieJiang | Existing deep agent initiative |
| Execution Worker Initiative | `project/initiatives/execution_worker/1_requirements.md` | ShaojieJiang | Worker execution model |
| Stack Runtime Image | `deploy/stack/Dockerfile.orcheo` | ShaojieJiang | Managed runtime image |
| Stack Compose | `deploy/stack/docker-compose.yml` | ShaojieJiang | Backend/worker deployment topology |
| Claude Code quickstart | https://code.claude.com/docs/en/quickstart | Anthropic | Install and login flows |
| Codex CLI docs | https://developers.openai.com/codex/cli | OpenAI | Install and login flows |
| Codex non-interactive docs | https://developers.openai.com/codex/noninteractive | OpenAI | `codex exec` automation mode |

## PROBLEM DEFINITION

### Objectives
Enable Orcheo workflows to delegate code- and agent-style tasks to the actual Claude Code and Codex CLIs, using a worker-managed runtime layer that keeps V1 setup simple and operationally understandable. Preserve a small configuration surface by using strong defaults and avoiding new user-facing environment variables unless they are clearly necessary.

### Target users
- Self-hosted Orcheo operators who want to automate coding-agent tasks from workflows.
- Workflow authors who want Claude Code or Codex to act as execution steps rather than only using model APIs.
- Teams already using the Orcheo execution worker and willing to authenticate external agent CLIs on worker hosts.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Workflow author | Add a Claude Code node to a workflow | I can use Claude Code as a coding agent step | P0 | Node is available in the registry, accepts a prompt and workspace inputs, and returns stdout/stderr/result metadata |
| Workflow author | Add a Codex node to a workflow | I can use Codex as a coding agent step | P0 | Node is available in the registry, accepts a prompt and workspace inputs, and returns stdout/stderr/result metadata |
| Operator | Let Orcheo install the required CLI on first use | I do not need to pre-bake agent binaries into every image refresh | P0 | Worker installs the CLI into the managed runtime directory when absent |
| Operator | Have Orcheo check for updates on a fixed cadence | I can keep agent runtimes current without manual weekly maintenance | P0 | Worker performs an out-of-band version check at a fixed default interval and upgrades outside the node hot path |
| Workflow author | Receive clear setup guidance when the worker is not authenticated | I know how to log in and rerun the workflow successfully | P0 | Node fails with actionable commands and a structured setup-needed error |
| Operator | Avoid adding many new environment variables | V1 stays simple to operate and document | P0 | V1 introduces no new user-facing env vars for versioning, install roots, or upgrade cadence |
| Workflow author | Use sane defaults without understanding provider-specific package management | The feature works with minimal setup choices | P0 | Defaults cover install location, upgrade cadence, auth checks, and command invocation |
| Platform team | Add more external agent providers later | The architecture does not fork per-provider logic repeatedly | P1 | Claude Code and Codex share a generic parent runtime and node contract |
| Workflow author | Resume successful use after upgrading the CLI | I benefit from current provider capabilities without rebuilding Orcheo | P1 | Worker records the resolved CLI version used for each run |
| Operator | Keep a working runtime available when maintenance or upgrades fail | Latest-channel updates do not break already-functioning workflows | P0 | New runtimes are staged side-by-side, manifests switch only after successful install + probe, and the last known-good runtime remains runnable until cleanup |
| Operator | Bound disk and process growth from external agent runtimes | Worker hosts remain predictable under repeated installs and concurrent runs | P0 | V1 defines retention/cleanup behavior for superseded runtimes and does not create extra background fan-out beyond the worker’s existing run concurrency |
| Canvas user | Add and configure these nodes in Canvas | Workflow management interface stays aligned with backend node support | P1 | Canvas exposes both nodes in the catalog and supports editing prompt, working directory, timeout, and provider-safe defaults |

### Context, Problems, Opportunities

Orcheo already supports deep agents and external coding-agent workflows through the browser context bridge, but it does not yet let a workflow run invoke the actual Claude Code or Codex products as execution nodes. The opportunity is to make these agents callable from the execution worker using the CLIs users already know, while keeping the first release operationally simple.

The main challenge is not invoking a binary; it is owning the lifecycle around install state, upgrade cadence, authentication state, and worker topology. Backend and worker processes may run separately, and multi-worker deployments can drift if runtime state is stored globally or managed ad hoc. V1 should therefore avoid a broad configuration surface and avoid in-band upgrades during workflow execution, while still supporting latest-channel maintenance by default.

### Product goals and Non-goals

**Goals:**
- Add `ClaudeCodeNode` and `CodexNode` as first-class workflow nodes.
- Add a shared `ExternalAgentNode` base class and runtime manager.
- Use the actual CLIs for invocation in V1; do not depend on provider SDKs.
- Support latest-channel installation with a fixed, built-in maintenance cadence.
- Install runtimes into a managed persistent directory, not global system paths.
- Detect unauthenticated runtimes and return exact manual login commands.
- Keep V1 configuration minimal by using defaults instead of new env vars.
- Record the resolved runtime version in node/run metadata for debugging.

**Non-goals:**
- SDK-based integration for Claude Code or Codex.
- Full pause-and-resume workflow semantics for blocked login/setup states.
- Multi-workspace hosted SaaS support in V1.
- Arbitrary per-provider tuning knobs exposed as environment variables.
- Exact version pinning, support matrices, or rollout channels in V1.
- Generic package manager support beyond what the provider CLIs require.

## PRODUCT DEFINITION

### Requirements

**P0: Node types**
- Add `ClaudeCodeNode` and `CodexNode` under the AI node category.
- Both nodes inherit from a shared `ExternalAgentNode` parent that handles prompt resolution, workspace preparation, runtime checks, process execution, result normalization, and trace metadata.

**P0: Managed runtime directory**
- V1 installs agent runtimes into a versioned directory managed by Orcheo.
- Default runtime root is derived automatically:
  - Use `/data/agent-runtimes` when `/data` is present and writable.
  - Otherwise use `~/.orcheo/agent-runtimes`.
- V1 does not add a new user-facing environment variable for overriding this path.

**P0: Provider bootstrap details**
- V1 standardizes on provider-published CLI packages installed into provider-owned prefixes inside the managed runtime root rather than global system paths.
- Codex uses the published `@openai/codex` CLI package and invokes `codex exec` for non-interactive automation.
- Claude Code uses the published `@anthropic-ai/claude-code` CLI package and invokes a non-interactive/task mode rather than the full-screen TUI.
- Orcheo-specific configuration remains default-driven; provider-native authentication methods are still allowed when the provider requires them.

**P0: Install and maintenance policy**
- If the requested provider runtime is missing, Orcheo installs the latest supported provider CLI into the managed runtime directory before invocation.
- Orcheo keeps a local manifest per provider with installed version, install time, last maintenance check, and last successful auth probe.
- Orcheo checks for updates on a fixed default cadence of 7 days.
- Upgrade checks and upgrades happen outside the hot execution path when possible. Node execution may trigger a lightweight “maintenance due” signal, but it must not silently upgrade just before invoking the workflow step.
- Installs and upgrades are staged side-by-side in versioned directories. Manifest pointers only move after the new runtime passes install verification and any required health probes.
- Failed maintenance checks, failed upgrades, or network errors must leave the last known-good runtime active.

**P0: Authentication handling**
- Orcheo does not manage provider OAuth tokens directly in V1.
- Each provider CLI uses its own native login flow and credential storage.
- Before invocation, the runtime manager performs a cheap auth probe.
- If auth is missing or invalid, the node returns a structured setup-needed failure that includes concrete commands for the operator to run on the worker host, then instructs the user to rerun the workflow.
- Operator guidance must document the provider differences that matter in practice:
  - Claude Code login is interactive and must be completed through the Claude CLI on the worker host.
  - Codex supports saved CLI login and provider-native API-key auth for `codex exec`; Orcheo does not wrap or rename those provider credentials.

**P0: Invocation model**
- Node invocation uses each provider’s non-interactive/scriptable CLI mode rather than the full-screen TUI.
- Node config supports a prompt, optional system instructions, optional working-directory inputs, timeout, and provider-specific safe defaults required for successful automation.
- Node output includes:
  - normalized status
  - stdout
  - stderr
  - exit code
  - resolved provider/runtime version
  - command metadata suitable for debugging
- If a process exits non-zero, crashes, or times out, the node still returns partial stdout/stderr plus a normalized failure status and reason.

**P0: Working-directory validation**
- Working-directory inputs must resolve to an existing directory before invocation.
- Raw inputs containing parent-directory traversal are never trusted directly; Orcheo validates the fully resolved path.
- V1 rejects obviously unsafe targets such as `/`, the worker home directory, and the managed runtime root itself.
- For coding-agent execution, the resolved directory must be a Git worktree root or a descendant inside a Git worktree.

**P0: Security and operational defaults**
- V1 is documented and positioned for self-hosted Orcheo only.
- Runtime binaries are not installed with `npm i -g` into global system locations.
- Runtime binaries execute under the existing worker OS user.
- V1 adds no new public HTTP endpoints and no new user-facing environment variables.

**P0: Concurrency and runtime integrity**
- Runtime installation, manifest updates, and maintenance for a given provider must be serialized across worker processes that share the same runtime root.
- Manifest writes must be atomic so concurrent workers never read partially written state.
- In-flight node runs pin the executable path/version they resolved at start so background maintenance cannot swap binaries underneath an active execution.

**P0: Resource guardrails**
- V1 keeps the execution model simple: one node invocation maps to one external agent process tree, bounded by the node timeout and the worker’s existing OS/container limits.
- Maintenance prunes superseded runtime directories after a successful upgrade, while retaining at least the current runtime and one previous known-good version for rollback/debugging.

**P1: Shared maintenance support**
- Add a worker-local maintenance service that can be called from startup hooks or a future scheduled job.
- Expose internal maintenance hooks so the runtime layer can be extended to more providers later.

**P1: Better blocked-run UX**
- Introduce a future `blocked_setup` or resumable run state after V1 if rerun-only guidance proves too coarse.

### Designs (if applicable)
- Design document: `project/initiatives/external_agent_cli_nodes/2_design.md`

### Other Teams Impacted
- **Execution Worker:** Gains runtime-management responsibilities for external agent binaries.
- **Canvas Frontend:** Needs catalog entries and inspector form support for the new node types if they are to be first-class in visual authoring.
- **Documentation:** Needs operator docs for login, maintenance behavior, and runtime expectations.

## TECHNICAL CONSIDERATIONS

### Architecture Overview
This initiative fits into the existing worker-executed workflow architecture. The new runtime layer lives beside the existing node system and is used only by the new external agent nodes. It relies on the current stack image already including Node.js, but it avoids global npm installs and instead manages versioned binaries inside a persistent runtime directory.

### Technical Requirements
- Use a shared runtime manager for provider install, maintenance checks, auth probes, and command resolution.
- Persist runtime manifests on disk in the managed runtime directory.
- Keep the upgrade cadence and install root as code-level defaults in V1, not environment variables.
- Ensure node execution is deterministic enough for debugging by recording the resolved runtime version and executable path.
- Make failures actionable: every missing-auth or install failure must produce exact commands and enough stderr/stdout to diagnose the issue.
- Serialize provider-local maintenance/install work with file- or directory-level locking that works across worker processes.
- Use atomic manifest writes and side-by-side version directories so a failed upgrade cannot corrupt runtime state.
- Capture partial process output on timeout/crash and classify failures consistently (`failed` vs `setup_needed`).

### AI/ML Considerations (if applicable)

#### Data Requirements
No new training or inference data is required. External agent CLIs operate against workflow-provided prompts and workspace inputs.

#### Algorithm selection
Not applicable. The initiative delegates to provider-managed coding agents through their CLIs.

#### Model performance requirements
The feature must be operationally reliable rather than model-benchmarked. The main quality bars are successful invocation, clear failure modes, and predictable setup behavior.

## MARKET DEFINITION (for products or large features)
This is an internal platform feature for self-hosted Orcheo deployments. External market sizing is out of scope.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Successful external-agent runs | >= 90% success rate in authenticated self-hosted environments during the first month |
| [Primary] Operator setup success | Most missing-auth incidents resolved via documented commands without code changes |
| [Secondary] Adoption | At least 3 internal or pilot workflows use Claude Code or Codex nodes within 30 days |
| [Guardrail] Runtime drift incidents | Zero incidents caused by silent in-band upgrades during execution |

### Rollout Strategy
Roll out behind an internal feature gate or documented “experimental/self-hosted only” marker. Start with Codex and Claude Code nodes in development and controlled self-hosted environments. Observe install, auth, and maintenance behavior before broadening deployment guidance.

### Estimated Launch Phases

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal development environments | Build runtime manager, nodes, and operator docs |
| **Phase 2** | Self-hosted pilot deployments | Validate auth guidance, maintenance cadence, and worker behavior |
| **Phase 3** | General self-hosted availability | Publish stable operator documentation and default workflow examples |

## HYPOTHESIS & RISKS

**Hypothesis:** Workflow authors want to orchestrate actual coding agents, not just LLM APIs, and a CLI-first integration with low setup friction is enough to drive early adoption in self-hosted Orcheo deployments.

**Risk:** Latest-channel upgrades may introduce breaking CLI behavior. **Risk mitigation:** Keep upgrades out of the execution hot path, record the resolved version per run, and document that V1 is self-hosted/experimental.

**Risk:** Multi-worker deployments can drift if runtime state is stored globally or per-container. **Risk mitigation:** Store binaries and manifests in a managed persistent runtime directory, serialize provider-local maintenance/install work, and keep the V1 support target to self-hosted environments.

**Risk:** Authentication failures may confuse operators. **Risk mitigation:** Always return explicit commands and rerun guidance instead of vague auth errors.

**Risk:** Runtime directories can grow unbounded or lose the last working version during upgrades. **Risk mitigation:** Retain the current and previous known-good runtime per provider and prune only after successful maintenance.

## APPENDIX
- Future extensions can add exact version pinning, env-var overrides, and provider-specific support matrices after V1 usage validates the defaults.
