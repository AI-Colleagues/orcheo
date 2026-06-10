# Project Plan

## For External Agent CLI Nodes — Claude Code and Codex as Workflow Nodes

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-31
- **Status:** Approved

---

## Overview

Implement a CLI-first external-agent runtime for Orcheo so workflows can invoke the actual Claude Code and Codex products from the execution worker. The first release uses latest-channel installs, a fixed maintenance cadence, manual provider login, and a small default-driven configuration surface.

**Related Documents:**
- Requirements: `project/initiatives/external_agent_cli_nodes/1_requirements.md`
- Design: `project/initiatives/external_agent_cli_nodes/2_design.md`

---

## Milestones

### Milestone 1: Shared External Agent Runtime Layer

**Description:** Build the generic runtime manager and manifest store used by all external agent providers. Success means Orcheo can resolve a persistent runtime root, install a provider runtime, store metadata, and decide whether maintenance is due.

#### Task Checklist

- [x] Task 1.1: Create `src/orcheo/external_agents/__init__.py`
  - Dependencies: None
- [x] Task 1.2: Create runtime path helpers with default root selection (`/data/agent-runtimes` or `~/.orcheo/agent-runtimes`)
  - Dependencies: None
- [x] Task 1.3: Create manifest models and persistence helpers for installed runtimes
  - Dependencies: Task 1.2
- [x] Task 1.3a: Add provider-local filesystem locking and atomic manifest writes
  - Dependencies: Tasks 1.2-1.3
- [x] Task 1.4: Implement maintenance-due logic with a fixed default interval of 7 days
  - Dependencies: Task 1.3
- [x] Task 1.5: Implement shared process execution helpers for non-interactive CLI invocation, partial-output capture, timeout enforcement, and process-group cleanup
  - Dependencies: None
- [x] Task 1.6: Add working-directory validation helpers for safe Git-worktree execution
  - Dependencies: Task 1.2
- [x] Task 1.7: Add unit tests for runtime root resolution, manifest persistence, locking, validation, and maintenance logic
  - Dependencies: Tasks 1.2-1.6

---

### Milestone 2: Provider Adapters for Claude Code and Codex

**Description:** Implement provider-specific install, auth probe, login guidance, and command construction for Claude Code and Codex.

#### Task Checklist

- [x] Task 2.1: Create provider protocol/interface for external agent adapters
  - Dependencies: Milestone 1
- [x] Task 2.2: Implement Claude Code provider adapter
  - Dependencies: Task 2.1
- [x] Task 2.3: Implement Codex provider adapter
  - Dependencies: Task 2.1
- [x] Task 2.4: Add install-latest flow using provider-specific commands into versioned runtime directories with post-install verification
  - Dependencies: Tasks 2.2-2.3
- [x] Task 2.5: Add auth probes and structured manual login instructions for both providers
  - Dependencies: Tasks 2.2-2.3
- [x] Task 2.6: Add maintenance rollback behavior so failed upgrades do not replace the last known-good runtime
  - Dependencies: Tasks 2.4-2.5
- [x] Task 2.7: Add unit tests for provider command builders, version parsing, install verification, and auth-probe handling
  - Dependencies: Tasks 2.2-2.6

---

### Milestone 3: Orcheo Nodes and Worker Integration

**Description:** Expose the provider runtimes as workflow nodes and ensure worker executions produce normalized results and actionable setup-needed failures.

#### Task Checklist

- [x] Task 3.1: Create `ExternalAgentNode` base class with prompt resolution, timeout handling, and result normalization
  - Dependencies: Milestones 1-2
- [x] Task 3.2: Create `ClaudeCodeNode` and register it in the node registry
  - Dependencies: Task 3.1
- [x] Task 3.3: Create `CodexNode` and register it in the node registry
  - Dependencies: Task 3.1
- [x] Task 3.4: Add trace metadata capturing provider, resolved runtime version, and command path
  - Dependencies: Task 3.1
- [x] Task 3.5: Ensure missing-auth flows return structured setup-needed results with exact commands and rerun guidance
  - Dependencies: Tasks 3.2-3.3
- [x] Task 3.6: Add failure normalization for non-zero exits, timeouts, and partial-output retention
  - Dependencies: Tasks 3.2-3.5
- [x] Task 3.7: Add integration tests for missing-runtime install, missing-auth, timeout, and successful invocation flows
  - Dependencies: Tasks 3.2-3.6

---

### Milestone 4: Documentation, Examples, and V1 Hardening

**Description:** Document the feature clearly for self-hosted operators, keep the configuration surface intentionally small, and validate the V1 operational defaults.

#### Task Checklist

- [x] Task 4.1: Document self-hosted-only support and manual login expectations
  - Dependencies: Milestone 3
- [x] Task 4.2: Document the default maintenance behavior: latest installs, fixed 7-day checks, no inline upgrades
  - Dependencies: Milestone 3
- [x] Task 4.3: Document provider bootstrap details, including the supported install path and auth differences for Claude Code vs Codex
  - Dependencies: Milestone 3
- [x] Task 4.4: Add example workflows or snippets showing Claude Code and Codex node usage
  - Dependencies: Milestone 3
- [x] Task 4.5: Validate that V1 introduces no new user-facing environment variables and does not require new Orcheo env vars for install roots, cadence, or provider selection
  - Dependencies: Milestone 3
- [x] Task 4.6: Define retention/cleanup behavior for superseded runtimes and verify it does not remove the current or previous known-good version
  - Dependencies: Milestone 3
- [x] Task 4.7: Add Studio node-catalog / inspector support requirements to the delivery checklist
  - Dependencies: Milestone 3
- [x] Task 4.8: Run `make format`, `make lint`, and the smallest relevant backend/node test targets
  - Dependencies: Milestones 1-4

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-31 | Codex | Initial draft |
