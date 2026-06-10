# Project Plan

## For Ambient Coding Agent — Browser Context Bridge

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-03-21
- **Status:** Approved

---

## Overview

Deliver the Browser Context Bridge feature in three phases: local HTTP server with context store first (enabling CLI testing without any frontend or backend changes), then Studio integration for a complete end-to-end relay, then onboarding polish for GA.

**Related Documents:**
- Requirements: `project/initiatives/ambient_coding_agent/1_requirements.md`
- Design: `project/initiatives/ambient_coding_agent/2_design.md`

---

## Milestones

### Milestone 1: HTTP Server with Local Context Store

**Description:** Ship the `orcheo browser-aware` CLI command with a local in-memory context store and context relay HTTP endpoints. No backend or Studio changes required. Agents interact with workflows via existing CLI commands (`orcheo workflow show`, `orcheo workflow download`, etc.). Context can be seeded by manually POSTing to `localhost:3333/context` during alpha testing.

#### Task Checklist

- [x] Task 1.1: Implement `BrowserContextStore` — in-memory store keyed by `session_id` with 300-second TTL eviction and focus-priority resolution using `last_focused_at` timestamp. Single-user (runs locally in HTTP server process)
  - Dependencies: None
- [x] Task 1.2: Implement context relay HTTP endpoints on the HTTP server — `POST /context` (upsert), `GET /context` (active context with focus-priority, `staleness_seconds`, `total_sessions`), `GET /context/sessions` (all sessions). CORS enabled for Studio origin. Bound to `localhost` only
  - Dependencies: Task 1.1
- [x] Task 1.3: Document that agents use existing CLI commands for workflow operations — `orcheo workflow show`, `orcheo workflow download`, `orcheo workflow upload`, `orcheo workflow create`, etc. No additional tool definitions needed
  - Dependencies: None
- [x] Task 1.4: Implement `orcheo browser-aware` command — starts a plain HTTP server (default `localhost:3333`) serving context relay endpoints; `--port` flag
  - Dependencies: Task 1.2
- [x] Task 1.5: Write unit tests for `BrowserContextStore` (TTL, focus-priority), context relay endpoints (CORS, upsert, GET response), and CLI commands (`orcheo context`, `orcheo context sessions`). Smoke test for `orcheo browser-aware`
  - Dependencies: Task 1.4
- [x] Task 1.6: Implement `orcheo context` and `orcheo context sessions` CLI commands — thin wrappers that hit `GET /context` and `GET /context/sessions` on the localhost HTTP server started by `orcheo browser-aware`
  - Dependencies: Task 1.2

---

### Milestone 2: Studio Integration — End-to-End Context Relay

**Description:** Enable automatic context relay from Studio browser tabs to the local HTTP server. After this milestone the core UX is live: open a workflow in Studio, run `orcheo browser-aware`, and Claude Code sees the active workflow without any manual steps.

#### Task Checklist

- [x] Task 2.1: Implement `BrowserContextProvider` React component — generates stable `sessionId` from `sessionStorage`; posts page identity (`page`, `workflow_id`, `workflow_name`, `focused`) to `http://localhost:3333/context` on `setPageContext()` call; attaches `visibilitychange`/`focus`/`blur` listeners; starts/stops 5-second heartbeat based on visibility. Silently handles connection failures (HTTP server not running)
  - Dependencies: Milestone 1
- [x] Task 2.2: Mount `BrowserContextProvider` in `App.tsx`; call `setPageContext()` in `WorkflowGallery` (on load) and `WorkflowStudio` (on workflow load)
  - Dependencies: Task 2.1
- [x] Task 2.3: Write unit tests for `BrowserContextProvider` — fires POST to localhost on route change, heartbeat start/stop on visibility change, focus flag accuracy, graceful failure when HTTP server is down
  - Dependencies: Task 2.1
- [ ] Task 2.4: End-to-end manual QA: open Studio gallery → `orcheo context` returns gallery page; navigate to workflow → context updates within 2 seconds; open two tabs → `orcheo context sessions` shows both
  - Dependencies: Tasks 2.2, 2.3

---

### Milestone 3: GA Polish

**Description:** Add onboarding UI polish and documentation for general availability.

#### Task Checklist

- [x] Task 3.1: Add "Connect your agent" section to Studio Settings — CLI quickstart instructions with copy button (links to `orcheo auth login` for token setup), active session count indicator
  - Dependencies: Milestone 2
- [x] Task 3.2: Write and publish onboarding documentation — "Connect Claude Code to Orcheo Studio" and "Connect Cursor to Orcheo Studio" guides
  - Dependencies: Milestones 1, 2
- [x] Task 3.3: Run `make lint`, `make test`, `make studio-lint`, `make studio-test` — all green with zero errors
  - Dependencies: All previous tasks

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-21 | Claude | Initial draft |

---

## Rollback / Contingency

- Context relay (`BrowserContextProvider`) can be disabled via feature flag without affecting any other Studio functionality.
- The context store runs locally in the HTTP server process — no backend impact. If issues arise, the developer simply stops `orcheo browser-aware`.
- Milestone 1 requires no backend changes at all; rollback is a CLI version downgrade only.
