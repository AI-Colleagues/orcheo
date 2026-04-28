# Requirements Document

## METADATA
- **Authors:** Claude
- **Project/Feature Name:** Ambient Coding Agent — Browser Context Bridge
- **Type:** Feature
- **Summary:** Expose Orcheo Canvas context (current page, active workflow, script source) to external coding agents (Claude Code, Codex, Cursor, etc.) via a local HTTP server and CLI commands, enabling agents to read and modify workflows without leaving the user's own terminal or IDE. Users bring their own agents and subscriptions.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-21

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Architecture — CLI Tool Design | `project/architecture/cli_tool_design.md` | ShaojieJiang | Orcheo CLI design |
| Prior Initiative — Python-Only Workflow Composition | `project/initiatives/python_only_workflow_composition/1_requirements.md` | ShaojieJiang | Python-only composition requirements |
| Design | `project/initiatives/ambient_coding_agent/2_design.md` | ShaojieJiang | Ambient coding agent design |
| Plan | `project/initiatives/ambient_coding_agent/3_plan.md` | ShaojieJiang | Ambient coding agent plan |

## PROBLEM DEFINITION

### Objectives
Enable developers to use their preferred AI coding agents (Claude Code, Codex, Cursor, etc.) to read and modify Orcheo workflows from their local terminal, with the agent always aware of what the user is looking at in Canvas.

### Target users
- Developers who use Claude Code, Codex, or Cursor locally and want those agents to act on their Orcheo workflows
- Teams that prefer a vibe-coding-first workflow authoring experience with an ambient AI assistant
- Platform operators who want to offer a CLI-driven interface without owning the agent compute

### User Stories

| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Developer with Claude Code | Run `orcheo browser-aware` and have Claude Code know which workflow I have open in Canvas | Claude Code can read and update my workflow without me copying and pasting scripts | P0 | Claude Code calling `orcheo context` returns the active Canvas page and workflow |
| Developer | Have the context update automatically when I switch tabs or navigate to a different workflow | I don't have to restart or re-configure my HTTP server | P0 | Navigating in Canvas from gallery to a workflow canvas updates the context returned by `orcheo context` within 2 seconds |
| Developer | Have the active (or last-visited) Canvas tab automatically used as the context source | I can work with multiple Canvas tabs open without manually selecting which one feeds my coding agent | P1 | Canvas tracks the active tab (or last-visited tab when all are inactive) and `orcheo context` returns that tab's context without explicit pinning |
| Developer | Have context relay work without any backend changes or extra deployment | I can start using ambient coding immediately after installing the CLI update | P1 | Context store runs inside `orcheo browser-aware` process; no new backend endpoints required |

### Context, Problems, Opportunities

Orcheo workflows are authored as Python LangGraph scripts. The natural place to write and modify these is a code editor or terminal, not a drag-and-drop canvas. Developers can already use CLI commands to download workflow scripts, but AI coding agents (Claude Code, Codex, Cursor) lack awareness of which workflow the developer is currently viewing in Canvas — they cannot automatically stay in sync with the developer's Canvas context.

The opportunity is to make Canvas context available to any coding agent via a local HTTP server and CLI commands. The agent runs wherever the developer already works; Orcheo provides context and workflow tools. This delivers the ambient coding agent experience the developer ecosystem expects.

The adjacent space (VS Code Copilot, Claude Code, Cursor) has established that developers want AI assistance that is aware of their current working context. Orcheo's workflow canvas is the equivalent of the "current file" — it should be equally accessible to these agents.

### Product Goals and Non-goals

**Goals:**
- Expose Canvas context (current page, active workflow ID and name) to external coding agents via the Orcheo CLI (`orcheo browser-aware`), which starts a local HTTP server.
- Provide CLI commands for agents to inspect browser context sessions.
- Handle multiple open Canvas tabs gracefully without requiring agent reconfiguration.

**Non-goals:**
- Real-time collaborative editing or multi-user context sharing across different user accounts.

## PRODUCT DEFINITION

### Requirements

**P0: Context relay (Canvas → local HTTP server)**
- Canvas posts context on every page navigation and focus/visibility change to `POST http://localhost:3333/context`.
- Payload includes: `session_id` (stable per tab, `sessionStorage`-backed), `page` (`gallery` | `canvas` | `other`), `workflow_id`, `workflow_name`, `focused` (bool), `timestamp`.
- The relay communicates *what* the user is viewing, not the content. Script source, version, and other workflow metadata are fetched via existing CLI commands (`orcheo workflow show/download`).
- Context relay only works when `orcheo browser-aware` is running — which is exactly when it is needed.
- Heartbeat every 5 seconds while the tab is visible; stops when hidden or closed.
- Context entries expire after 300 seconds without a heartbeat (tab considered closed).

**P0: Context store (local, inside `orcheo browser-aware` process)**
- In-memory store inside the HTTP server process. No backend endpoints required.
- `POST /context` (on HTTP server port) — upserts a session entry. Canvas posts here.
- `GET /context` — returns the active context: the session with the most recent `last_focused_at`, or the most recently seen if none have focus history. Includes `session_id`, `page`, `workflow_id`, `workflow_name`, `staleness_seconds`, `total_sessions`. Used by CLI commands; also queryable directly.
- `GET /context/sessions` — returns all active sessions (used by `orcheo context sessions` CLI command).
- CORS: HTTP server allows requests from the Canvas origin so the browser can POST context.
- No auth on context relay endpoints — they are bound to `localhost` and only accept connections from the local machine.

**P0: CLI — `orcheo browser-aware` command (single new command)**
- `orcheo browser-aware` — start a plain HTTP server on `localhost:3333`. This is the only new CLI command.
- `orcheo browser-aware --port <port>` — custom port.
Session diagnostics are exposed as CLI commands, accessible through the agent or directly from the terminal.

**P0: CLI commands for context access**
- `orcheo context` — get the active context (current Canvas page, workflow ID, name, staleness, session count). Thin wrapper hitting `GET http://localhost:3333/context`.
- `orcheo context sessions` — list all active Canvas sessions. Thin wrapper hitting `GET http://localhost:3333/context/sessions`.
- Agents interact with Orcheo via these CLI commands (e.g., `orcheo context`, `orcheo workflow show`, `orcheo workflow download`). No special protocol or SDK integration is required — any agent that can invoke shell commands can participate.

**P1: Canvas Settings — "Connect your agent" UI**
- New section in Canvas Settings with CLI setup instructions (links to `orcheo auth login` for token setup).
- Displays active session count and last-seen time as a connection health indicator.
- Not P0 because developers onboard via `orcheo browser-aware` from the terminal; this UI is polish for discoverability.


### Designs (if applicable)
See `project/initiatives/ambient_coding_agent/2_design.md`.

### Other Teams Impacted
- **Canvas Frontend:** New context relay (JS) posting to localhost HTTP server, and (P1) Settings UI section.
- **CLI/SDK (`packages/sdk`):** New commands: `orcheo browser-aware` (HTTP server), `orcheo context` and `orcheo context sessions` (context access). HTTP server logic (including context store and context relay endpoints) lives in CLI package.

## TECHNICAL CONSIDERATIONS

### Architecture Overview

```
Canvas (browser tab)
  └── context relay: POST http://localhost:3333/context on navigation + focus events + heartbeat

Local machine (developer)
  orcheo browser-aware (localhost:3333)
    ├── Serves context relay HTTP endpoints (POST/GET /context, GET /context/sessions) for Canvas
    ├── In-memory context store with 300s TTL
    └── CLI commands (orcheo context, orcheo context sessions) query the HTTP server

Claude Code / Codex / Cursor
  └── Invokes CLI commands (orcheo context, orcheo workflow show/download, etc.)
```

### Technical Requirements
- Context store is in-memory inside the `orcheo browser-aware` process; no backend changes or persistent DB schema required.
- `orcheo browser-aware` starts a plain HTTP server on `localhost:3333` (configurable via `--port`) for the context relay and context access endpoints.
- Context relay runs in the browser via a `BrowserContextProvider` React component mounted at the app level, which fires on page navigation and `visibilitychange`/`focus` DOM events. The relay sends only page identity (page type, workflow ID, name), not workflow content. Posts to `localhost:3333/context`.
- Multi-tab disambiguation is resolved locally in the HTTP server using `last_focused_at` timestamps; clients do not need to coordinate.
- `orcheo browser-aware` requires a valid Orcheo API token (same `ORCHEO_SERVICE_TOKEN` / `orcheo auth login` flow already defined in `cli_tool_design.md`) for workflow API calls. Context relay endpoints on localhost do not require auth.
- CORS: the HTTP server's context relay endpoints allow requests from the Canvas origin (HTTPS → HTTP localhost is permitted by modern browsers as localhost is treated as a secure context).
- Canvas Settings links to the existing `orcheo auth login` flow for token setup; no new auth primitives needed.

## LAUNCH/ROLLOUT PLAN

### Success metrics

| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] `orcheo browser-aware` adoption | ≥ 20% of active developer users run `orcheo browser-aware` within 30 days of GA |
| [Secondary] Workflow updates via CLI | ≥ 10% of workflow version pushes originate from CLI commands within 60 days |
| [Guardrail] Context staleness | p95 staleness of `GET http://localhost:3333/context` < 5 seconds when a Canvas tab is open |
| [Guardrail] Local resource usage | `orcheo browser-aware` process uses < 50 MB memory and < 1% CPU at steady state |

### Rollout Strategy

Two main phases: `orcheo browser-aware` with local context store first (no backend or frontend changes needed), then Canvas context relay integration and polish. The context relay can be shipped behind a feature flag on the Canvas side to allow gradual rollout.

### Estimated Launch Phases

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal engineering | `orcheo browser-aware` command with local context store and context relay HTTP endpoints. No Canvas integration yet — developers can test by manually POSTing to `localhost:3333/context`. No backend changes. |
| **Phase 2** | Beta users / developer waitlist | Canvas context relay enabled (`BrowserContextProvider` posts to localhost); end-to-end flow works. P1 CLI commands (session listing) added. |
| **Phase 3** | GA | "Connect your agent" Settings UI. Full documentation and onboarding guide. |

## HYPOTHESIS & RISKS

**Hypothesis:** Developers who already use Claude Code or Codex will adopt `orcheo browser-aware` as their primary workflow authoring interface once they can operate on their Canvas context without leaving the terminal. This will increase workflow version push volume and daily active engagement with the platform.

**Risks:**
- Multi-tab context ambiguity may confuse users if the wrong session's context is served. Mitigation: `orcheo context` returns `total_sessions` and `staleness_seconds` so agents can surface a warning; `orcheo context sessions` (P1) provides diagnostic visibility. Focus-priority resolution (most recently focused tab wins) handles disambiguation automatically.
- Browser tab heartbeat adds a small constant load to the local HTTP server. Mitigation: heartbeat is 5-second interval, POST body is < 500 bytes (page identity only — no script content), and the endpoint does a single in-memory upsert. Load impact is negligible at current scale.


## APPENDIX
