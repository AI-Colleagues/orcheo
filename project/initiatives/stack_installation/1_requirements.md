# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Stack Installation Simplification and Version Awareness
- **Type:** Enhancement
- **Summary:** Replace the current multi-step manual install/upgrade experience with a guided single-command flow and add proactive version visibility/reminders across Studio and CLI.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-02-21

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | Shaojie Jiang | Stack Installation Design |
| Project Plan | `./3_plan.md` | Shaojie Jiang | Stack Installation Plan |
| Current Setup Guide | `docs/manual_setup.md` | Shaojie Jiang | Manual Setup |
| Canonical Bootstrap Path | `https://ai-colleagues.com/install.sh` | Shaojie Jiang | Unix bootstrap entrypoint |
| Local Stack Asset Source | `deploy/stack/` | Shaojie Jiang | Local compose/Dockerfile/env/widget assets |
| Studio App | `apps/studio/` | Shaojie Jiang | Studio UI |
| CLI Entrypoint | `packages/sdk/src/orcheo_sdk/cli/main.py` | Shaojie Jiang | Orcheo CLI |

## PROBLEM DEFINITION
### Objectives
Ship a single guided setup/upgrade command so users can install or upgrade Orcheo stack components with prompts and defaults. Add version visibility and update reminders in Studio and CLI to reduce version skew and stale deployments.

### Target users
- First-time Orcheo users installing locally.
- Existing users upgrading CLI/backend/canvas.
- Operators who connect Studio and CLI to self-hosted backends.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| New user | run one command to install Orcheo | I can get started without reading multiple setup docs | P0 | Canonical bootstrap one-liner completes install with defaults and delegates to guided setup |
| Existing user | run one command to upgrade my stack | I can stay current with minimal effort | P0 | Guided flow supports upgrade mode and preserves config |
| Studio user | see Studio and backend versions in UI | I understand version skew immediately | P0 | UI shows both versions in a stable location |
| Studio user | get prompted when newer versions exist | I know when to upgrade | P0 | Non-blocking reminder appears when update is available |
| CLI user | receive update reminders on CLI use, at most once per 24 hours | I stay up to date without noisy output | P0 | Update checks run only when CLI commands are invoked, and at most once every 24h |

### Context, Problems, Opportunities
Current setup historically relied on manual steps and guide-following. The current canonical bootstrap path and guided CLI flow improve this, but documentation and implementation need to stay aligned around one source of truth for stack assets and startup behavior. Studio currently does not expose its own version and connected backend version side-by-side, and CLI has no periodic update reminder flow. This creates avoidable support load and version skew between `orcheo-sdk`, `orcheo-backend`, and `orcheo-studio`.

### Product goals and Non-goals
Goals:
- Reduce install/upgrade to one guided command or bootstrap script with safe defaults.
- Update the `docs/` landing page, README.md, and `homepage/` with the newest shortest installation path.
- Surface Studio + backend versions directly in Studio UI.
- Remind CLI users about CLI/backend updates on first run in each 24h window.
- Keep reminders non-blocking and failure-tolerant.

Non-goals:
- Automatic background upgrades without user confirmation.
- Replacing advanced manual setup docs for custom enterprise topologies.
- Enforcing upgrades via hard blocks in CLI or Studio.

## PRODUCT DEFINITION
### Requirements
P0 installation flow:
- Provide one canonical one-command bootstrap entrypoint for Unix (macOS/Linux) that works without preinstalled `orcheo-sdk` and without preinstalled `uv` (for example, `curl ... | sh`). The bootstrap script installs `uv` if missing, then delegates to `uvx orcheo-sdk install` for the guided flow.
- Keep install/upgrade orchestration logic in a single CLI implementation (bootstrap only handles environment detection, prerequisite setup, and handoff).
- Windows support is P1 for full validation/hardening; a PowerShell bootstrap entrypoint exists and follows the same thin handoff pattern.
- Support two primary modes: fresh install and in-place upgrade.
- Prompt for key inputs with defaults (backend URL, auth mode, optional stack start). Supported auth modes (e.g., API key, OAuth) and credential handling during setup should be defined in the design doc.
- For secrets that can be safely generated on the local machine (for example, stack `SECRET_KEY` values), setup should default to generating them for the user. Prompt for manual entry only when required by explicit user choice or external integration constraints.
- Support non-interactive mode (`--yes` + flags) for CI/scripts.
- Validate prerequisites and print actionable remediation (Docker for stack startup).
- If `uv` is missing, bootstrap should install it (or provide exact install commands) and continue.
- If Docker is missing, prompt users to either install Docker (default choice) or skip Docker-dependent steps when they plan to use a remote backend. Without Docker, users can still install and use the CLI and SDK against a remote backend; local full-stack mode (backend + Studio + Redis + worker) requires Docker.
- For local full-stack startup, provision required compose assets from a canonical source (`deploy/stack/`) into a user-managed stack directory (default `~/.orcheo/stack`) before running Docker Compose.
- Allow stack asset source and target overrides via environment variables (`ORCHEO_STACK_ASSET_BASE_URL`, `ORCHEO_STACK_DIR`) for mirrors and custom environments.
- Be idempotent: rerunning should reconcile state safely rather than duplicate/conflict.
- End with a summary that includes synced stack location, `.env` path, and next commands.

P0 version awareness:
- Add backend system/version metadata endpoint used by both Studio and CLI.
- In Studio (`apps/studio/`), show:
  - Studio version (running frontend)
  - Connected backend version
  - Update state when newer versions are available
- Show upgrade guidance in Studio when updates are available (link/commands).
- Run Studio update checks no more than once per 24h per browser/profile.

P0 CLI reminder behavior:
- Run update checks only during CLI command invocations (no background scheduler).
- On a CLI invocation, if at least 24h has passed since the previous update check attempt, check:
  - Installed CLI version vs latest CLI release
  - Connected backend version vs latest backend release
- Print concise reminders when updates are available.
- Suppress update reminders for non-stable local versions (for example, dev/nightly/private builds).
- Never fail user commands because update checks fail (network/registry errors are soft-fail).
- Allow explicit opt-out via env var/flag.

P1:
- Add compatibility matrix metadata to distinguish "available update" vs "recommended minimum" update.
- Add optional "what changed" release links in reminders.
- Provide rollback/recovery guidance when an upgrade fails mid-way (e.g., partial state detection and safe retry).
- Full Windows/PowerShell bootstrap support (see P0 note above).

### Designs (if applicable)
See `./2_design.md`.

### Other Teams Impacted
- SDK/CLI: setup command, update-check logic, reminder output.
- Backend: version/update metadata endpoint and registry cache.
- Studio: version display component and reminder UX.
- Docs/Developer Relations: update quickstart and upgrade guidance.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Use a shared version metadata contract exposed by backend and consumed by Studio/CLI. Implement setup as a single orchestrated flow in CLI tooling (plus optional bootstrap entrypoint for users without prior install).

### Technical Requirements
- Bootstrap entrypoints:
  - Unix shell bootstrap (`sh`) as the only P0 bootstrap entrypoint.
  - Windows PowerShell bootstrap is P1.
  - Bootstrap remains thin and delegates install/upgrade decisions to CLI flow.
- Stack assets:
  - Source-of-truth assets are stored in-repo under `deploy/stack/`.
  - Setup downloads missing assets from a configurable raw content base URL into
    `ORCHEO_STACK_DIR` (default `~/.orcheo/stack`) before compose startup.
- Source of truth package versions:
  - CLI: installed `orcheo-sdk`
  - Backend: installed `orcheo-backend`
  - Studio: installed `orcheo-studio`
- Latest version discovery via package registries (PyPI for Python packages, npm for Studio) with caching.
- Version comparison:
  - Python packages: `packaging.version`
  - Studio: semver-compatible comparison
- Reminder eligibility:
  - Only stable/public release versions are eligible for "update available" reminders.
  - Dev/nightly/private/local build identifiers are treated as non-remindable by default to avoid false-positive upgrade prompts in self-hosted/custom environments.
- 24h check window persisted locally:
  - CLI cache under `~/.cache/orcheo`
  - Studio cache in browser storage
- Update checks must include timeout and graceful fallback.

## MARKET DEFINITION (for products or large features)
Not applicable; this is a developer experience and platform-operability enhancement.

## LAUNCH/ROLLOUT PLAN
### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Setup completion rate | >=90% successful guided setup/upgrade without manual doc fallback. Measured via CLI exit codes and optional anonymous telemetry (opt-in) or user feedback surveys. |
| [Secondary] Time-to-first-run | Median setup completion under 10 minutes on supported local environments. Measured via CLI timing logs (local only, not transmitted). |
| [Secondary] Version visibility adoption | Studio version panel rendered on 100% authenticated sessions. Measured via Studio runtime checks. |
| [Guardrail] Reminder noise | <=1 reminder block per CLI user per 24h window. Enforced by local cache timestamp. |

### Rollout Strategy
- Release behind feature flags/config toggles where needed.
- Start with internal dogfooding on local and self-hosted environments.
- Enable by default after stability and UX validation.

### Estimated Launch Phases
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Installer | Ship guided setup/upgrade command and docs |
| **Phase 2** | Studio + Backend | Ship backend version API and Studio version/reminder UI |
| **Phase 3** | CLI | Ship 24h update-check reminders for CLI + backend |
| **Phase 4 (P1)** | Windows | Add PowerShell bootstrap parity for Windows setup |
| **Phase 5 (P1)** | Compatibility + Recovery | Add compatibility matrix semantics, release-note links, and rollback/recovery guidance |

## HYPOTHESIS & RISKS
Hypothesis:
- A single guided command plus proactive version awareness reduces setup failure, shortens onboarding time, and lowers support requests related to stale environments.

Risks:
- Registry/network failures could create false negatives for update availability.
- Reminder UX could become noisy if cache windows are not enforced correctly.
- Version comparison edge cases (pre-releases/local builds) could produce incorrect prompts.

Risk Mitigation:
- Soft-fail all update checks and preserve command execution success.
- Add strict 24h gating and opt-out controls.
- Include comprehensive version parsing tests (stable, prerelease, local build tags).

## APPENDIX
- Canonical bootstrap entrypoint: `https://ai-colleagues.com/install.sh`
- Canonical stack asset path: `deploy/stack/`
