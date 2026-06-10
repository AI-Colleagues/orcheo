# Project Plan

## For Bundled Caddy Ingress for Standard Self-Hosted Installs

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-04-09
- **Status:** Approved

---

## Overview

Bundle Caddy into the standard Orcheo stack as the recommended public ingress tier for reachable self-hosted installations. The plan covers stack assets, setup flow, runtime configuration, docs, and validation. Tunnel-based development remains supported but out of scope for this implementation.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 1: Scope and stack contract

**Description:** Finalize the deployment contract for bundled Caddy and the distinction between local-only installs and public self-hosted installs.

#### Task Checklist

- [x] Task 1.1: Finalize initiative scope and confirm that bundled Caddy targets reachable self-hosted hosts, not tunnel replacement
  - Dependencies: None
- [x] Task 1.2: Define the public-origin env contract for Studio, backend, and ChatKit
  - Dependencies: Task 1.1
- [x] Task 1.3: Decide whether backend/canvas raw ports stay published in public-ingress mode or become debug-only
  - Dependencies: Task 1.1
- [x] Task 1.4: Define the supported initial topology for replica load balancing
  - Dependencies: Task 1.1

---

### Milestone 2: Stack asset changes

**Description:** Add Caddy assets and wire them into the bundled stack.

#### Task Checklist

- [x] Task 2.1: Add Caddy service definition to `deploy/stack/docker-compose.yml`
  - Dependencies: Milestone 1
- [x] Task 2.2: Add a Caddy configuration file for Studio, `/api/*`, and `/ws/*` routing
  - Dependencies: Task 2.1
- [x] Task 2.3: Persist Caddy state required for automatic certificate management
  - Dependencies: Task 2.1
- [x] Task 2.4: Add stack-asset tests or validation for the new ingress files
  - Dependencies: Tasks 2.1, 2.2

---

### Milestone 3: Setup and configuration flow

**Description:** Extend `orcheo install` so operators can opt into bundled public ingress and generate a coherent runtime configuration.

#### Task Checklist

- [x] Task 3.1: Add setup prompts and flags for public-ingress mode and hostname input
  - Dependencies: Milestone 1
- [x] Task 3.2: Generate the env values required for public-origin routing
  - Dependencies: Task 3.1
- [x] Task 3.3: Validate or warn on missing hostname/network prerequisites in setup summaries
  - Dependencies: Task 3.1
- [x] Task 3.4: Preserve the current local-only installation path without regression
  - Dependencies: Task 3.1

---

### Milestone 4: Runtime validation and scaling behavior

**Description:** Verify that public ingress works for browser traffic and for replicated backend nodes of one logical deployment.

#### Task Checklist

- [x] Task 4.1: Add integration coverage for `/`, `/api/*`, and `/ws/*` through Caddy
  - Dependencies: Milestone 2
- [x] Task 4.2: Validate Studio API and WebSocket behavior through the public origin
  - Dependencies: Tasks 3.2, 4.1
- [x] Task 4.3: Validate multiple backend replicas behind Caddy against shared Postgres and Redis
  - Dependencies: Tasks 2.2, 3.2
- [x] Task 4.4: Confirm and document boundaries where cloud-managed ingress is preferable
  - Dependencies: Task 4.3

---

### Milestone 5: Documentation and rollout

**Description:** Publish operator-facing guidance for bundled ingress and clarify when tunnels are still needed.

#### Task Checklist

- [x] Task 5.1: Update `docs/manual_setup.md` with a public self-hosted ingress path
  - Dependencies: Milestones 2, 3
- [x] Task 5.2: Update `docs/deployment.md` with the bundled Caddy topology and prerequisites
  - Dependencies: Milestones 2, 3
- [x] Task 5.3: Update `docs/environment_variables.md` with any new or revised ingress-related variables
  - Dependencies: Milestone 3
- [x] Task 5.4: Clarify in docs when Cloudflare Tunnel remains the right tool
  - Dependencies: Milestone 3
- [ ] Task 5.5: Run end-to-end QA on a reachable self-hosted host
  - Dependencies: Milestones 3, 4

---

## Validation Gates

- Python changes: `make format`, `make lint`, and targeted `uv run pytest ...` for CLI/backend stack configuration tests.
- Studio changes: `make studio-format`, `make studio-lint`, and targeted Studio tests if frontend config behavior changes.
- Stack validation: compose-level smoke tests for HTTPS routing, API proxying, and WebSocket proxying.
- Deployment validation: manual public-domain test on a reachable self-hosted host with DNS and open `80/443`.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-04-09 | Codex | Initial draft |
| 2026-04-09 | Codex | Implemented bundled Caddy ingress assets, setup flow, validation, and docs; reachable-host manual QA remains pending |
