# Design Document

## For Stack Installation Simplification and Version Awareness

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-02-21
- **Status:** Approved

---

## Overview

This design introduces three connected improvements:
1. A guided, single-command setup/upgrade experience for Orcheo stack installation.
2. Studio UI visibility for both Studio version and connected backend version, plus upgrade reminders.
3. CLI first-run-per-24h update checks for CLI and backend versions with non-blocking reminders.
4. Stable-release-only reminder policy; development/private builds do not emit reminder prompts.

The implementation uses a shared backend metadata contract for installed/latest versions and lightweight client-side caches (24h) in both CLI and Studio. The setup flow is implemented in CLI tooling so users can run one command with prompts or pass flags for non-interactive execution.

## Components

- **Bootstrap Entrypoints (`scripts/bootstrap/` or release assets)**
  - Unix bootstrap (`curl ... | sh`) is P0 and targets macOS/Linux.
  - PowerShell bootstrap path is available and follows the same handoff model.
  - Bootstrap is intentionally thin: detect/install `uv` if missing, then hand off to
    `uvx orcheo-sdk install` with stack asset base URL defaults.
  - Key dependencies: shell/Pwsh runtime, package installer commands, environment probes.

- **CLI Setup Orchestrator (`packages/sdk/src/orcheo_sdk/cli/main.py`, setup command module under `packages/sdk/src/orcheo_sdk/cli/setup.py`)**
  - Provides `orcheo install` command (and supports bootstrap usage via `uvx`).
  - Handles prerequisite checks, prompt collection, install/upgrade execution, and summary output.
  - Key dependencies: existing CLI config/state modules, shell command runner, package managers (uv/npm).

- **Stack Asset Bundle (`deploy/stack/`)**
  - Stores source-of-truth compose assets (`docker-compose.yml`, `Dockerfile.orcheo`,
    `.env.example`, `chatkit_widgets/*`).
  - Consumed by setup flow via raw-content download into a user-managed stack directory
    (`ORCHEO_STACK_DIR`, default `~/.orcheo/stack`).
  - Key dependencies: GitHub raw delivery (or mirror override via `ORCHEO_STACK_ASSET_BASE_URL`).

- **CLI Update Notifier (`packages/sdk/src/orcheo_sdk/cli/main.py`, cache helper under `packages/sdk/src/orcheo_sdk/cli/cache.py`, version-check helper under `packages/sdk/src/orcheo_sdk/cli/update_check.py`)**
  - Runs once per 24h window at CLI startup.
  - Compares installed CLI/backend versions against latest known versions.
  - Key dependencies: cache manager, backend system info endpoint, version parser.

- **Backend System Info Router (`apps/backend/src/orcheo_backend/app/routers/system.py`, wired from `apps/backend/src/orcheo_backend/app/main.py`)**
  - Exposes version/update metadata used by Studio and CLI.
  - Caches upstream registry checks to avoid repeated outbound requests.
  - Key dependencies: `importlib.metadata`, HTTP client for PyPI/npm registry calls.

- **Studio Version Status UI (`apps/studio/src/features/shared/components/top-navigation/version-status.tsx`, API client integration in `apps/studio/src/lib/api.ts`)**
  - Displays running Studio version and connected backend version in a persistent UI location.
  - Shows reminder when newer versions are available.
  - Key dependencies: top navigation/settings UI, API client, local storage.

## Request Flows

### Flow 1: Guided setup or upgrade

1. User runs `orcheo install` (or bootstrap equivalent).
2. Bootstrap (if used) ensures `uv` exists; if missing, it installs `uv` or prints exact
   install commands and retries handoff.
3. CLI checks prerequisites (Docker for stack startup).
4. CLI prompts for mode and key inputs, defaulting sensible values on Enter.
   - Mode: fresh install or in-place upgrade.
   - Backend URL.
   - Auth mode.
   - Optional stack startup.
   - If Docker is missing: default prompt is to install Docker, with explicit skip option for
     remote-backend-only usage.
4. CLI performs install or upgrade steps:
   - Stack asset bootstrap/sync (`deploy/stack` -> `ORCHEO_STACK_DIR`)
   - `.env` reconciliation with setup-selected values
   - Optional `docker compose ... pull` then `docker compose ... up -d` against the provisioned stack directory
5. In upgrade mode, CLI reconciles state idempotently and preserves existing configuration.
6. CLI validates resulting versions and backend reachability (if configured).
7. CLI prints summary and next steps.

### Setup auth modes and credential handling

Supported setup auth modes (P0):
- **API key**: prompt for API key with hidden input; save to local config or environment
  variable export snippet only after explicit user confirmation.
- **OAuth/device login**: open browser/device flow where available and store resulting token in
  existing secure credential/config store used by CLI.

Rules:
- Credentials are never echoed in plaintext in terminal output.
- When a required secret can be generated locally with a secure random source (for example,
  local development signing/session keys), setup defaults to auto-generating and storing it
  for the user. Manual secret entry is an explicit opt-out path.
- Non-interactive mode supports auth flags/env vars (for example, `--auth-mode`, `--api-key`
  or equivalent env var inputs).
- Setup summary confirms auth mode selection without printing secret values.

### Flow 2: Studio version visibility and reminder

1. Studio initializes and reads local app version from build metadata.
2. Studio calls backend system info endpoint.
3. Studio renders:
   - Studio current version
   - Backend current version
   - Update badges/reminder state
4. If updates exist, Studio shows non-blocking reminder with upgrade guidance.
5. Studio stores last-check timestamp and suppresses repeated checks until 24h passes.

### Flow 3: CLI first-run update reminder (24h window)

1. On CLI invocation, startup loads the update-check cache entry.
2. If last check is less than 24h ago, skip check.
3. Otherwise, CLI fetches backend system info metadata.
4. CLI resolves installed local CLI version and connected backend version.
5. CLI compares installed vs latest and prints reminder if updates exist.
   - Reminders are shown for stable/public local versions by default.
   - Development/nightly/private local versions are suppressed.
6. CLI stores check timestamp regardless of update/no-update result.

## API Contracts

### Backend system info endpoint

```
GET /api/system/info
Headers:
  Accept: application/json
  Authorization: Bearer <token>

Response 200:
{
  "backend": {
    "package": "orcheo-backend",
    "current_version": "0.4.0",
    "latest_version": "0.5.0",
    "update_available": true
  },
  "cli": {
    "package": "orcheo-sdk",
    "latest_version": "0.5.0"
  },
  "canvas": {
    "package": "orcheo-studio",
    "latest_version": "0.4.2"
  },
  "checked_at": "2026-02-21T13:00:00Z"
}
```

Behavior:
- `latest_version` can be `null` when registry lookup fails.
- Endpoint is served from the authenticated `/api` router (same as other protected
  API resources).
- Backend caches registry lookups for a configurable TTL.

## Data Models / Schemas

### CLI update check cache payload

```json
{
  "last_checked_at": "2026-02-21T13:00:00Z",
  "profile": "default",
  "api_url": "http://localhost:2025",
  "last_result": {
    "cli_update_available": true,
    "backend_update_available": false
  }
}
```

### Studio local storage payload

```json
{
  "lastCheckedAt": "2026-02-21T13:00:00Z",
  "latest": {
    "cli": "0.5.0",
    "backend": "0.5.0",
    "canvas": "0.4.2"
  }
}
```

## Security Considerations

- `GET /api/system/info` must not expose secrets, tokens, internal DSNs, or private host metadata.
- Require authentication and enforce read scope (for example, `system:read`) before
  returning version metadata.
- Sanitize/validate all registry response payloads before returning to clients.
- Add timeout and bounded retries for outbound registry calls.
- Apply endpoint-specific rate limits at the API gateway and app layer:
  - Authenticated callers: 30 requests/minute per user token (`sub` claim).
  - Unauthenticated/invalid-token callers: 10 requests/minute per source IP.
  - Burst allowance: token bucket with burst size 10, refill over 60 seconds.
  - On limit exceed, return `429 Too Many Requests` with `Retry-After` seconds header.

## Performance Considerations

- Backend registry queries are cached in-memory (default TTL: 12 hours).
- Studio and CLI check frequency is capped at once every 24 hours per user/profile.
- Endpoint payload is small and static-like; no database access required.
- Update checks are non-blocking and should not delay main command execution materially.

## Testing Strategy

- **Unit tests**
  - Version parsing/comparison (stable, prerelease, local build tags).
  - 24h gating logic in CLI and Studio cache readers.
  - Setup prompt defaults and non-interactive overrides.
- **Integration tests**
  - Backend `/api/system/info` happy path and registry failure fallback.
  - Backend `/api/system/info` rejects unauthenticated callers.
  - CLI startup reminder behavior with mocked backend metadata.
  - Studio fetch + render of version badge/reminder states.
- **Manual QA checklist**
  - Fresh install flow from clean machine profile.
  - Upgrade flow from older installed versions.
  - Studio connected to outdated backend shows reminder.
  - CLI reminder appears once, then suppresses for 24h.

## Rollout Plan

1. Phase 1: Ship installer/upgrade command with docs and internal dogfooding.
2. Phase 2: Ship backend system info endpoint and Studio version/reminder UI.
3. Phase 3: Enable CLI 24h reminder checks by default.
4. Phase 4 (P1): Add PowerShell bootstrap parity for full Windows setup support.

Feature flags/config:
- `ORCHEO_DISABLE_UPDATE_CHECK=1` disables CLI update reminders.
- `ORCHEO_UPDATE_CHECK_TTL_HOURS` controls backend cache/check windows.
- `ORCHEO_STACK_DIR` controls stack project directory for compose assets.
- `ORCHEO_STACK_ASSET_BASE_URL` controls raw asset source for stack provisioning.

## P1 Extensions

- Add compatibility matrix metadata so clients can distinguish:
  - update available (optional)
  - recommended minimum (actionable/priority)
- Attach optional release-notes links ("what changed") in Studio/CLI reminders.
- Extend setup/upgrade summary and docs with rollback/recovery guidance for partial upgrades.
- Add Windows PowerShell bootstrap parity after Unix bootstrap is stable.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-21 | Codex | Initial draft |
