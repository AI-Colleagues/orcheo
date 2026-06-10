# Design Document

## For Ambient Coding Agent — Browser Context Bridge

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2026-03-21
- **Status:** Approved

---

## Overview

This feature exposes Orcheo Studio context to external AI coding agents (Claude Code, Codex, Cursor, etc.) via a local HTTP server and CLI commands. The HTTP server is shipped as part of the Orcheo CLI (`orcheo browser-aware`) and calls the Orcheo REST API directly for workflow operations.

The Studio browser tab relays its current context (page, active workflow) to a lightweight localhost endpoint. The CLI commands read from this endpoint so any coding agent always knows what the user is looking at. Agents can then read and update workflows through CLI commands (`orcheo context`, `orcheo workflow show`, `orcheo workflow download`, etc.) without the user leaving their terminal.

Users bring their own coding agents and subscriptions. Orcheo provides context and workflow CLI commands.

## Components

- **Studio Context Relay (TypeScript, Studio team)**
  - Sends page identity (page type, workflow ID, name, focus state) to the local HTTP server on navigation, focus/visibility changes, and a 5-second heartbeat.
  - Does NOT send script content or version — the CLI commands fetch these from the workflow API.
  - Generates a stable `sessionId` per tab (stored in `sessionStorage`).
  - Integrated as a side-effect inside a new `BrowserContextProvider` mounted at the app level.

- **Orcheo CLI — `orcheo browser-aware` command (Python, SDK team)**
  - Single new CLI command. Starts a plain HTTP server on `localhost:3333` (configurable via `--port`) for context relay endpoints (`POST/GET /context`, `GET /context/sessions`).
  - In-memory context store keyed by `session_id` with 300-second TTL. Focus-priority resolution using `last_focused_at` timestamp.
  - Agents interact via CLI commands: `orcheo context`, `orcheo context sessions`, `orcheo workflow list`, `orcheo workflow show <id>`, `orcheo workflow download <id>`, `orcheo workflow upload --id <id> <file>`, `orcheo workflow upload <file>`. Workflow commands call the Orcheo REST API; context commands hit the local HTTP server.
  - Auth: uses the same `ORCHEO_SERVICE_TOKEN` / `~/.config/orcheo/cli.toml` profile for workflow API calls. Context relay endpoints on localhost do not require auth.
  - CORS enabled on context relay endpoints to allow Studio (HTTPS) to POST to localhost.

- **Studio Settings — "Connect your agent" UI (TypeScript, Studio team) — P1, Phase 3**
  - New section in Settings showing CLI setup instructions (links to `orcheo auth login` for token setup).
  - Displays active session count and staleness as a health indicator.
  - Not required for core functionality — developers onboard via `orcheo browser-aware` and CLI commands.

## Request Flows

### Flow 1: Context relay — tab navigation

1. User navigates to `/workflow-canvas/abc` in Studio.
2. `BrowserContextProvider` detects route change via React Router `useLocation`.
3. Provider calls `POST http://localhost:3333/context` with `{ session_id, page: 'canvas', workflow_id: 'abc', workflow_name: 'My Flow', focused: true }`.
4. HTTP server upserts the session entry in its local in-memory store; TTL reset to 300 seconds. If `focused` is true, `last_focused_at` is updated.
5. Tab starts heartbeat (every 5 seconds) while `document.visibilityState === 'visible'`. Heartbeats carry the same fields — a simple TTL refresh.
6. If `orcheo browser-aware` is not running, the POST silently fails — context relay is only active when the HTTP server is running.

### Flow 2: Agent reads current context

1. Developer runs `orcheo browser-aware` locally.
2. Claude Code runs `orcheo context` CLI command.
3. CLI command hits `GET http://localhost:3333/context` on the local HTTP server.
4. Returns the active session's context: `{ page: 'canvas', workflow_id: 'abc', workflow_name: 'My Flow', staleness_seconds: 3 }`.
5. Claude Code sees the current workflow context and runs `orcheo workflow show abc` to fetch the script and version when needed.

### Flow 3: Agent updates a workflow script

1. Developer asks Claude Code: "Add error handling to the main node."
2. Claude Code runs `orcheo workflow download abc` → receives current script.
3. Claude Code generates an updated script.
4. Claude Code runs `orcheo workflow upload --id abc updated_script.py`.
5. CLI command calls `POST /api/workflows/abc/versions/ingest` on the Orcheo backend with the updated script.
6. Backend ingests the new version.
7. Developer refreshes Studio (or it picks up the change on next focus) and sees the updated graph.

### Flow 4: Multi-tab disambiguation

1. Developer has two Studio tabs open: Tab A (gallery, focused 2 minutes ago) and Tab B (workflow X, focused 10 seconds ago).
2. Both tabs are backgrounded; developer switches to terminal.
3. Claude Code runs `orcheo context`.
4. HTTP server returns Tab B's context from its local store (most recently focused, within TTL). Response includes `total_sessions: 2`.
5. Agent runs `orcheo context sessions` to show both sessions with their pages and last-seen times. The developer can then specify which workflow to target by ID.

### Flow 5: Stale / no active session

1. Developer has no Studio tab open.
2. Claude Code runs `orcheo context`.
3. HTTP server reads from local store → `{ page: null, staleness_seconds: 120, total_sessions: 0 }`.
4. CLI command returns: `"No active Studio session found. Open Orcheo Studio in your browser to provide context."`.
5. Claude Code can still run other commands (`orcheo workflow list`, etc.) that don't depend on browser context.

## API Contracts

### Context relay endpoints (served by `orcheo browser-aware` on localhost)

These endpoints are served by the `orcheo browser-aware` HTTP server (default `localhost:3333`), not the Orcheo backend. No authentication is required — they are bound to localhost only.

```
POST /context
Body:
  session_id:       string   -- stable per-tab identifier (sessionStorage)
  page:             string   -- "gallery" | "canvas" | "other"
  workflow_id:      string | null
  workflow_name:    string | null
  focused:          bool          -- document.hasFocus() at time of post
  timestamp:        string        -- ISO 8601

Response:
  204 No Content

CORS: Allows requests from Studio origin.
```

```
GET /context

Response:
  200 OK ->
    {
      "session_id":        string | null,
      "page":              string | null,
      "workflow_id":       string | null,
      "workflow_name":     string | null,
      "focused":           bool,
      "last_focused_at":   string | null,  -- ISO 8601; null if never focused
      "staleness_seconds": int,
      "total_sessions":    int
    }
```

```
GET /context/sessions

Response:
  200 OK ->
    [
      {
        "session_id":        string,
        "page":              string,
        "workflow_id":       string | null,
        "workflow_name":     string | null,
        "focused":           bool,
        "last_seen":         string,   -- ISO 8601
        "last_focused_at":   string | null,  -- ISO 8601; null if never focused
        "staleness_seconds": int
      }
    ]
```

### CLI commands (agents invoke these directly)

All workflow commands call the existing Orcheo REST API. Context commands hit the local HTTP server started by `orcheo browser-aware`.

| Command | Underlying call |
|---------|----------------|
| `orcheo context` | `GET http://localhost:3333/context` — returns page, workflow ID/name, staleness, session count. |
| `orcheo context sessions` | `GET http://localhost:3333/context/sessions` — list all active Studio sessions. |
| `orcheo workflow list` | `GET /api/workflows` (Orcheo backend) |
| `orcheo workflow show <id>` | `GET /api/workflows/{ref}/versions` → latest version `graph.source` (Orcheo backend) |
| `orcheo workflow download <id>` | `GET /api/workflows/{ref}/versions` → downloads script to local file (Orcheo backend) |
| `orcheo workflow upload --id <id> <file>` | `POST /api/workflows/{ref}/versions/ingest` (Orcheo backend) |
| `orcheo workflow upload <file>` | `POST /api/workflows` then `POST /api/workflows/{ref}/versions/ingest` (Orcheo backend) |

## Data Models / Schemas

### BrowserContextEntry (HTTP server in-memory)

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Stable per-tab ID from `sessionStorage` |
| `page` | string | `"gallery"` \| `"canvas"` \| `"other"` |
| `workflow_id` | string \| null | Current workflow ID (canvas page only) |
| `workflow_name` | string \| null | Human-readable workflow name |
| `focused` | bool | Whether tab had focus at last update |
| `last_seen` | datetime | UTC timestamp of last POST |
| `last_focused_at` | datetime \| null | UTC timestamp of last time `focused` was true; null if never focused. Used for focus-priority resolution |

TTL: entries are evicted 300 seconds after `last_seen`. The store is local to the HTTP server process — inherently single-user since `orcheo browser-aware` runs on the developer's own machine.

### Context relay POST body (TypeScript → localhost HTTP server)

```typescript
interface BrowserContextPayload {
  session_id: string;
  page: 'gallery' | 'canvas' | 'other';
  workflow_id: string | null;
  workflow_name: string | null;
  focused: boolean;
  timestamp: string; // ISO 8601
}
```

## Security Considerations

- Context relay endpoints (`/context`, `/context/sessions`) are served on `localhost` only — no network exposure. No authentication required since they are local-only.
- Workflow API calls (list, read, update, create) require a valid bearer token (same auth as existing API).
- The context store holds only page identity (page type, workflow ID, name, focus state) — no script content or secrets. Entries are evicted with the TTL.
- The context store is inherently single-user: it runs inside the developer's own `orcheo browser-aware` process.
- `orcheo browser-aware` binds to `localhost` by default (not `0.0.0.0`).
- CORS: context relay endpoints allow requests from the Studio origin so the browser can POST. The allowed origin should be configurable or match the Orcheo server URL from the CLI profile.

## Performance Considerations

- In-memory context store in the HTTP server: O(1) upsert and read; no database queries, no network round-trips for context reads.
- `POST /context` body is < 500 bytes (page identity only — no script content). Single in-memory write per request.
- Heartbeat interval is 5 seconds per tab; the HTTP server handles only the local user's tabs — negligible load.
- Workflow CLI commands make one HTTP request per invocation to the Orcheo backend; no persistent connections to maintain.

## Testing Strategy

- **Unit tests**
  - `BrowserContextStore`: TTL eviction, focus-priority resolution, multi-session ordering.
  - `BrowserContextProvider` (React): fires POST on route change, `visibilitychange`, and `focus`; stops heartbeat on `visibilitychange=hidden`.
  - Each CLI command: correct underlying call made (local HTTP server or REST API), correct output format, graceful error handling.
  - Context relay HTTP endpoints: CORS headers, upsert behavior, GET response shape.

- **Integration tests**
  - POST `/context` then GET `/context` round-trip on local HTTP server returns correct context.
  - TTL eviction: session expires after 300 seconds without heartbeat.
  - Multi-session: two sessions with different focus states; GET returns most recently focused.
  - `orcheo workflow upload --id <id>` calls ingest endpoint and returns new version ID.

- **Manual QA checklist**
  - Open Studio on gallery → `orcheo context` returns `page: gallery`.
  - Navigate to a workflow → `orcheo context` updates within 2 seconds.
  - Open second Studio tab → `orcheo context sessions` returns two sessions.
  - Close one tab → session disappears from `orcheo context sessions` within 300 seconds.
  - Run `orcheo browser-aware` → ask Claude Code "what workflow am I looking at?" → agent runs `orcheo context` → correct answer.
  - Ask Claude Code to update the current workflow → refresh Studio to confirm the update.

## Rollout Plan

1. **Phase 1** — HTTP server with local context store: ship `orcheo browser-aware` command with context relay HTTP endpoints and all CLI commands (`orcheo context`, `orcheo workflow list/show/download/upload`). No backend or Studio changes. Developers can test the full loop by manually POSTing to `localhost:3333/context`.
2. **Phase 2** — Studio integration: enable context relay in Studio (`BrowserContextProvider` posts to localhost). End-to-end flow is fully functional.
3. **Phase 3** — Onboarding polish: Add "Connect your agent" Settings section. Publish onboarding guide.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-21 | Claude | Initial draft |
