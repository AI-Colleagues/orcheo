# Design Document

## For External Agent CLI Nodes — Claude Code and Codex as Workflow Nodes

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-31
- **Status:** Approved

---

## Overview

This initiative adds a new execution path in Orcheo for workflow nodes that call actual coding-agent CLIs rather than model-provider APIs. V1 supports two providers, Claude Code and Codex, and intentionally keeps the integration narrow: use the provider CLIs directly, install the latest version into an Orcheo-managed runtime directory, authenticate them manually using the provider’s native login flow, and return actionable setup guidance when the runtime is not ready.

The design avoids two classes of complexity in V1. First, it avoids SDK integration and provider-specific session protocols. Second, it avoids a broad configuration surface. Install roots, maintenance cadence, and latest-channel behavior are implemented as code defaults rather than user-facing environment variables. The runtime layer is shared across providers so that future agents can reuse the same lifecycle and node contract.

## Components

- **External Agent Runtime Manager (`src/orcheo/external_agents/runtime.py`)**
  - Owns provider installation, version discovery, maintenance scheduling, auth probes, manifest persistence, and command resolution.
  - Exposes a provider-agnostic interface used by nodes.

- **Provider Adapters (`src/orcheo/external_agents/providers/*.py`)**
  - One adapter for Claude Code and one for Codex.
  - Each adapter defines install command, maintenance check command, auth probe command, invocation command builder, and version parsing.

- **Runtime Manifest Store (`src/orcheo/external_agents/manifest.py`)**
  - Stores per-provider metadata in the managed runtime directory.
  - Tracks installed version, install timestamp, last maintenance check timestamp, and last successful auth probe timestamp.
  - Persists manifest updates atomically and under a provider-local lock so multiple workers cannot corrupt shared state.

- **External Agent Base Node (`src/orcheo/nodes/external_agent.py`)**
  - Shared node logic for prompt resolution, workspace setup, timeout handling, result normalization, and trace metadata.
  - Delegates provider-specific behavior to runtime manager + provider adapters.

- **Provider Nodes (`src/orcheo/nodes/claude_code.py`, `src/orcheo/nodes/codex.py`)**
  - Thin subclasses that select provider identity and provider-specific defaults.

- **Worker Integration (`apps/backend/src/orcheo_backend/worker/...`)**
  - Ensures runtime manager operates correctly in worker processes.
  - Optionally triggers maintenance checks outside the node hot path in future phases.

## Request Flows

### Flow 1: First invocation with missing runtime

1. Workflow run reaches `ClaudeCodeNode` or `CodexNode`.
2. Node resolves prompt, working directory, timeout, and provider identity.
3. External agent runtime manager computes the managed runtime root:
   - `/data/agent-runtimes` if `/data` is present and writable.
   - Otherwise `~/.orcheo/agent-runtimes`.
4. Runtime manager checks manifest and binary path for the provider.
5. If missing, runtime manager acquires a provider-local install lock and installs the latest provider CLI into a versioned staging directory under the runtime root.
6. Runtime manager verifies the staged binary, records resolved version metadata, and atomically updates the manifest pointer.
7. Node continues to auth probe and invocation.

### Flow 2: Runtime present but login missing

1. Node asks runtime manager for an auth probe.
2. Provider adapter runs a cheap, non-destructive command that distinguishes “CLI installed but not authenticated” from other failures.
3. If unauthenticated, node returns a structured setup-needed error:
   - provider name
   - binary path
   - resolved version
   - exact commands to run on the worker host
   - rerun guidance
4. Workflow run fails cleanly. V1 expects the user to rerun after login.

### Flow 3: Successful invocation

1. Runtime manager confirms binary exists and auth probe passes.
2. Runtime manager checks whether maintenance is due.
3. If maintenance is due, it records that state but does not upgrade inline with the execution step.
4. Provider adapter builds a non-interactive command using workflow prompt, workspace, and provider-specific safe defaults.
5. Node executes the process, captures stdout/stderr/exit code, and enforces timeout.
6. Node normalizes the result payload and attaches trace metadata including provider and resolved runtime version.

### Flow 4: Scheduled maintenance check

1. A maintenance entrypoint or future scheduled job asks runtime manager to run provider maintenance.
2. Runtime manager compares the current timestamp to the fixed maintenance cadence (7 days).
3. If due, provider adapter checks for a newer available CLI version and stages the upgrade into a new versioned directory while holding the provider-local maintenance lock.
4. Manifest is updated only after the new runtime passes install verification and any required health checks. Failed checks leave the existing runtime active.
5. Existing workflow runs are unaffected because upgrades are not performed mid-step and active runs keep the executable path they resolved at start.

## API Contracts

### Node JSON schema

```json
{
  "type": "ClaudeCodeNode",
  "name": "claude_fix",
  "prompt": "Review the checked-out project and fix failing tests.",
  "working_directory": "{{inputs.repo_path}}",
  "timeout_seconds": 1800
}
```

```json
{
  "type": "CodexNode",
  "name": "codex_refactor",
  "prompt": "Refactor the workflow implementation to reduce duplication.",
  "working_directory": "{{inputs.repo_path}}",
  "timeout_seconds": 1800
}
```

### Internal provider contract

```python
class ExternalAgentProvider(Protocol):
    name: str

    def install_latest(self, runtime_root: Path) -> ResolvedRuntime: ...
    def verify_runtime(self, runtime: ResolvedRuntime) -> None: ...
    def probe_auth(self, runtime: ResolvedRuntime) -> AuthProbeResult: ...
    def build_command(
        self,
        runtime: ResolvedRuntime,
        *,
        prompt: str,
        working_directory: Path | None,
        timeout_seconds: int,
    ) -> list[str]: ...
    def render_login_instructions(self, runtime: ResolvedRuntime) -> list[str]: ...
```

### Setup-needed node result

```json
{
  "status": "setup_needed",
  "provider": "claude_code",
  "resolved_version": "latest-resolved-version",
  "commands": [
    "claude",
    "# complete login in the interactive session"
  ],
  "message": "Claude Code is installed but not authenticated on this worker. Complete login and rerun the workflow."
}
```

## Data Models / Schemas

### Runtime manifest

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Stable provider identifier (`claude_code`, `codex`) |
| `install_root` | string | Absolute provider runtime root |
| `current_version` | string | Resolved installed version |
| `installed_at` | string | ISO 8601 install timestamp |
| `last_checked_at` | string | ISO 8601 maintenance-check timestamp |
| `last_auth_ok_at` | string \| null | ISO 8601 timestamp of latest successful auth probe |

### Normalized node result

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `succeeded`, `failed`, or `setup_needed` |
| `provider` | string | External agent provider |
| `resolved_version` | string | CLI version used for the run |
| `command` | list[string] | Executed command vector |
| `exit_code` | integer \| null | Process exit code |
| `stdout` | string | Captured stdout |
| `stderr` | string | Captured stderr |
| `message` | string \| null | User-facing summary or setup guidance |

## Provider Operational Assumptions

- **Codex**
  - Install from the published `@openai/codex` package into a provider-owned prefix under the managed runtime root.
  - Use `codex exec` as the non-interactive automation entrypoint.
  - Auth probe should distinguish between saved CLI login and provider-native API-key usage for `codex exec` without requiring Orcheo-specific env vars.

- **Claude Code**
  - Install from the published `@anthropic-ai/claude-code` package into a provider-owned prefix under the managed runtime root.
  - Use a non-interactive/task invocation path rather than the full-screen TUI.
  - Auth guidance should point operators to the interactive `claude` login flow on the worker host when credentials are missing.

## Failure Handling

- **Install failure**
  - If first-time install fails, the node returns `failed` with captured stdout/stderr and a message that identifies the failed provider command.
  - The manifest is not updated until install and version verification succeed.

- **Maintenance failure**
  - Network errors, registry failures, or provider health-check failures during maintenance are recorded in logs/trace metadata, but they do not evict the current runtime.
  - The next due maintenance window can retry from the still-working runtime.

- **Invocation crash / non-zero exit**
  - The node returns `failed`, preserving partial stdout/stderr and exit code when available.
  - Provider-specific parsing may attach a concise failure reason, but raw command output remains available for debugging.

- **Timeout**
  - The node terminates the provider process group, captures partial output, and returns a normalized timeout failure.
  - Timeout handling must include best-effort child-process cleanup so abandoned agent subprocesses do not accumulate on the worker.

## Security Considerations

- V1 is self-hosted only. It is not designed for shared multi-workspace runtimes.
- Runtime binaries are installed into an Orcheo-managed directory, not global system paths.
- Provider login is still owned by the provider CLI. Orcheo does not copy or reinterpret provider OAuth tokens in V1.
- Nodes execute with the same worker OS user already used by Orcheo.
- Invocation must use non-interactive CLI modes suitable for automation.
- Working-directory inputs are validated before execution:
  - Resolve symlinks and require the final path to exist as a directory.
  - Reject raw traversal attempts by validating the resolved path rather than trusting the original string.
  - Reject `/`, the worker home directory, and the managed runtime root as execution targets.
  - Require the resolved path to be a Git worktree root or a descendant inside a Git worktree.

## Concurrency and State Management

- Install, upgrade, and manifest mutation are serialized per provider using a filesystem lock in the shared runtime root so multiple worker processes cannot race.
- Manifest writes use write-to-temp + atomic rename semantics.
- Resolved runtimes are immutable once published. Maintenance writes a new version directory and flips the manifest only after verification, so active runs continue using the path they already resolved.

## Resource Management

- Runtime installs are versioned so maintenance can prune old versions without touching the active one.
- V1 retains the current runtime and one previous known-good runtime per provider, then removes older superseded directories after a successful maintenance cycle.
- Each node execution maps to one external agent process tree and relies on existing worker/container CPU and memory limits rather than introducing a second resource scheduler in V1.

## Performance Considerations

- First-use installs can be slow because they depend on package-manager/network availability.
- Normal run-path checks should be cheap: stat binary, read manifest, run a short auth probe.
- Maintenance checks are fixed-frequency and should avoid repeated network calls within the 7-day window.
- Inline upgrades are intentionally avoided to reduce unpredictable latency spikes in workflow execution.

## Testing Strategy

- **Unit tests**: Runtime-root resolution, manifest read/write, atomic manifest replacement, maintenance-due logic, provider command builders, auth-probe result parsing, working-directory validation, and node result normalization.
- **Integration tests**: Install-missing flow, setup-needed flow, successful invocation flow, timeout handling, version recording, maintenance rollback on failure, and runtime reuse across repeated/concurrent runs.
- **Manual QA checklist**: Authenticate Claude Code on a worker, authenticate Codex on a worker, run both nodes successfully, verify rerun guidance when logged out, verify maintenance does not upgrade inline, and verify the Canvas node catalog renders the new nodes once backend registration is enabled.

## Rollout Plan

1. Phase 1: Implement runtime manager, provider adapters, and provider nodes behind an internal flag.
2. Phase 2: Validate behavior in local/self-hosted worker environments with real CLI authentication.
3. Phase 3: Publish operator docs and enable the nodes for general self-hosted usage.

Backwards compatibility notes:
- No new public API endpoints are introduced in V1.
- No new required environment variables are introduced in V1.
- Future pinning or env-based overrides can be layered on later without changing the node contract.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-31 | Codex | Initial draft |
