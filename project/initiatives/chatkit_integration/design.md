# ChatKit Integration Design

## Overview
This document describes the system design for integrating ChatKit into Orcheo across two client surfaces (public page, Canvas chat bubble) and a unified backend endpoint. The design targets incremental delivery while minimizing duplicated UI logic.

## Components
- **Canvas Frontend (TypeScript/React)**
  - Adds floating chat bubble, modal container, and shared ChatKit widget.
  - Responsible for requesting JWTs from Canvas backend and passing them to ChatKit client.
- **Public Chat Frontend (Canvas app shell)**
  - Static route served under `${canvas_base_url}/chat/:workflowId`; the page keeps all state in-memory so refreshes clear any cached data.
  - Loads the same ChatKit widget bundle used by Canvas, initializing with read-only workflow metadata (name only) plus the workflow identifier.
  - Public access is controlled exclusively via publish/unpublish plus the `require_login` flag—no shared secrets are distributed.
  - If the workflow was published with `require_login=true`, prompts the visitor to complete OAuth (e.g., Google) before instantiating the widget.
- **CLI & MCP Publishing UX (orcheo CLI + orcheo-mcp)**
  - Adds `orcheo workflow publish` / `unpublish` commands with interactive prompts and flags (e.g., `--require-login`) plus post-run summaries displaying the shareable URL.
  - Shares the same command registry with the MCP server so `orcheo-mcp` exposes `workflows.publish`, `workflows.unpublish`, and enriched `list_workflows` / `show_workflow` data for assistants like Claude/Codex.
  - Persists the last-published workflow state locally to show status in `orcheo workflow list`, and returns identical metadata through MCP responses (including `is_public`, `require_login`, and share link).
  - Surfaces errors (e.g., missing permissions, invalid workflow) inline with actionable remediation hints while MCP propagates the same errors as structured tool failures.
  - Canvas-side publish UX parity is explicitly deferred until after the CLI/MCP flows ship; these two surfaces act as the authoritative entry points for initial rollout.
- **Canvas Backend (FastAPI)**
  - Provides publish/unpublish APIs.
  - Issues workflow-scoped JWTs for authenticated editors.
- **OAuth/Auth Provider**
  - Reuses existing Canvas OAuth clients; issues short-lived session cookies for public visitors when login is required.
- **ChatKit Backend (FastAPI existing)**
  - Single `/api/chatkit` endpoint supporting both auth modes.
  - Orchestrates workflow invocation, handles streaming responses, and enforces rate limits by reusing the shared middleware in `apps/backend/src/orcheo_backend/app/authentication/rate_limit.py`.
- **Persistence**
  - Workflow table gains `is_public`, `published_at`, `published_by`, and `require_login`.
  - JWT signing keys stored in secure config (through environment variable `ORCHEO_AUTH_JWT_SECRET`).
  - ChatKit transcripts (public or Canvas) persist via existing session store with default infinite retention unless a future policy sets TTLs.

## Request Flows
### 1. Canvas Chat Bubble (authenticated)
1. User opens workflow in Canvas editor.
2. Clicking chat bubble triggers `/api/workflows/{id}/chatkit/session` call.
3. Backend validates user permissions and issues JWT: `{sub, workflow_id, permissions, exp}`.
4. Modal instantiates ChatKit widget with `authMode = "jwt"` and uses `Authorization: Bearer <token>` on websocket/HTTP.
5. ChatKit backend verifies signature, checks workflow access, executes workflow, streams response.
6. On token expiry (~5 min), frontend refreshes via silent request.

### 2. Public Chat Page (open access + optional OAuth)
1. Owner publishes workflow and shares the URL `.../chat/{workflowId}`; no secrets are embedded in the link.
2. Visitor loads page; Canvas app fetches workflow metadata (name only) with workflowId and determines whether login is required.
3. If `require_login=true` and no session cookie exists, visitor is sent through OAuth (state param ties back to the workflowId and return URL). After OAuth success, session cookie is set.
4. Widget initializes with `authMode = "publish"` and issues requests containing `{workflow_id}` while relying on the browser's cookies for OAuth, when required.
5. ChatKit backend confirms the workflow is still public and, if `require_login=true`, validates the OAuth subject before executing the workflow.
6. Rate limiter tracks per workflow identifier, per IP, and per OAuth user (when available).
7. If the owner unpublishes the workflow, new sessions stop immediately while existing chats keep streaming until they end or disconnect.

### 3. CLI & MCP Publish/Unpublish flow
1. A user runs `orcheo workflow publish <workflow_id> [--require-login]` locally, or an AI assistant triggers the mirrored `orcheo-mcp.workflows.publish` tool; both paths fetch workflow metadata, honor the `--require-login` intent (prompting when omitted), and confirm public exposure.
2. Shared helpers invoke `POST /api/workflows/{id}/publish` with the selected options and return the resulting share URL once; MCP responses include the same payload shape so assistants can narrate the link without persisting anything sensitive.
3. Subsequent `orcheo workflow unpublish <workflow_id>` commands and the matching MCP tools hit the revoke endpoint, update cached status, and return consistent status objects (public/private, require-login flag).
4. CLI exit codes reflect success/failure for scripting, while MCP tool responses encode identical error codes/messages so AI clients can guide users through remediation without bespoke logic.

## API Contract
```
POST /api/chatkit
Headers:
  Authorization: Bearer <jwt>   # optional
Body fields:
  workflow_id: str
  messages: ChatMessage[]
  client_id: str (for dedupe)
  oauth_session: bool (implicit via cookie)

Responses:
  200 OK -> stream or JSON
  401 Unauthorized -> missing/invalid token
  403 Forbidden -> workflow unpublished or access denied
  429 Too Many Requests -> rate limit triggered
```

### Session issuance
```
POST /api/workflows/{workflow_id}/chatkit/session
Headers: Cookie auth
Body: {}
Response 200: { token: <jwt>, expires_in: 300 }
```

### Publish management
```
POST   /api/workflows/{id}/publish         -> marks public + sets require_login flag
POST   /api/workflows/{id}/publish/revoke  -> unpublish
```

## Frontend Architecture
- Introduce shared ChatKit client module under `apps/canvas/src/features/chatkit/`.
- Modal component reads workflow context, lazy-loads widget bundle to keep editor light.
- Public page imports the same widget but passes `readOnly: true`, public auth mode (no tokens), and hides editor-specific controls.
- Use React context or Zustand store for chat state; sessions persist via ChatKit backend so the widget only needs in-memory cache for current display.

## Security Considerations
- No publish tokens are persisted or distributed; revocation relies on unpublish or OAuth requirements.
- Strict CORS rules on backend to only allow Canvas/public domains.
- JWT secret rotation via env var; default signing key comes from `ORCHEO_AUTH_JWT_SECRET` and should be rotated regularly (kid header for seamless rollover).
- OAuth login flow uses PKCE + state tokens to prevent CSRF; tokens stored in HttpOnly cookies.
- Instrument abuse monitoring: log auth failures (without tokens), emit metrics by workflow/OAuth user/IP, and feed dashboards owned by Platform Admin + SRE on-call; active CAPTCHA or challenge flows remain future work beyond this phase.

## Testing Strategy
- Unit tests for publish/unpublish logic (backend + shared CLI/MCP command helpers).
- Integration tests for `/api/chatkit` verifying both auth paths.
- Frontend tests for modal open/close, login prompts, and open-access flows.
- Manual QA checklist covering:
  - Publish workflow -> share link -> chat works unauthenticated.
  - Publish workflow with login required -> OAuth prompt -> chat works after auth.
  - Unpublish -> public link fails with 403.
  - Canvas modal respects workflow switch and token expiry.
  - Transcript persistence verified by reloading page and pulling session history via backend tools.

## Rollout Plan
1. Implement backend publish metadata + APIs behind flag.
2. Deploy public page but keep publish flag disabled until backend load testing complete.
3. Ship Canvas chat bubble + JWT flow gated by `chatkit_canvas_enabled` flag for internal users.
4. Gradually enable publish feature per workspace and monitor metrics.

## Open Issues
- Determine hosting path for public assets when canvas app is deployed separately.
- Canvas publishing UX should mirror the CLI/MCP flows (same prompts/options) as follow-up work once these implementations land, to keep user expectations aligned across all surfaces.
