# ChatKit Integration Requirements

## Summary
Enable two chat experiences powered by Orcheo workflows:
1. **Public chat page** for any published workflow, optionally requiring OAuth login depending on the publisher’s choice.
2. **Canvas chat bubble** for authenticated creators to test the workflow inline.

Both surfaces must reuse a single ChatKit backend endpoint while enforcing the correct authentication path (public workflow id + optional OAuth vs. JWT) and respecting workflow publish status. The ChatKit widget code must be shared between public and Canvas surfaces to minimize divergence.
Publishing, unpublishing, and rotating workflows are initiated through the Orcheo CLI and mirrored via the MCP server so human operators and AI assistants share the same UX until the Canvas UI catches up.

## Goals
- Let workflow owners publish a workflow and share a public chat URL that works from the open internet.
- Provide Canvas users with an in-editor chat bubble for rapid testing of the workflow currently being edited.
- Maintain a single authoritative ChatKit backend contract so both clients behave consistently.
- Allow owners to revoke or change access at any time (unpublish, toggle login requirements, expire JWTs).
- Ensure CLI and MCP surfaces expose identical publish/unpublish workflows so automations and AI copilots can manage sharing without bespoke logic.

## Non-goals
- Building a generic workflow marketplace or discovery surface.
- Supporting multi-workflow sessions inside the Canvas chat bubble.
- Implementing advanced moderation features beyond basic rate limiting/CAPTCHA.

## Personas
- **Workflow Creator (authenticated)**: logged-in Canvas user editing workflows, needs quick testing.
- **Public Tester (anonymous)**: anyone with the published link who wants to try the workflow.
- **Platform Admin**: needs visibility into publish state, revocation, and abuse controls.

## User Stories
| # | Persona | When | I want | So that |
|---|---------|------|--------|---------|
| 1 | Creator | I publish a workflow for external testing | Choose whether OAuth login is required and receive a shareable URL | I can control public access without extra tooling |
| 2 | Creator | A workflow is already public | Unpublish or enable login instantly | Existing links stop working if compromised |
| 3 | Creator | I am editing a workflow in Canvas | Open a chat bubble scoped to that workflow using my auth context | I can validate behavior without leaving the editor |
| 4 | Public tester | I open a shared link | Chat with the workflow (authenticating if prompted) and see its name | I can exercise the workflow confidently |
| 5 | Platform admin | I monitor published workflows | Audit status and abuse metrics | I can disable violating workflows quickly |

## Functional Requirements
- **Publish Workflow**
  - Introduce explicit "Publish" action (delivered via the CLI and mirrored through the MCP server, with Canvas UX tracked as future work) that flips `workflow.is_public = true` and lets the owner opt into requiring OAuth login.
  - Store published metadata: published_at, publisher_id, `require_login` flag, OAuth client context.
  - Provide an "Unpublish" action; revocation happens instantly for new sessions while existing chats continue until they naturally complete.
- **Tooling parity**
  - CLI commands (`orcheo workflow publish|unpublish`) and MCP tools (`workflows.publish|unpublish`) must share the same command registry, prompts, flag defaults, and exit codes/error payloads.
  - `orcheo workflow list/show` output and MCP `list_workflows`/`show_workflow` responses must include identical publish metadata (`is_public`, `require_login`, share URL preview) so scripts and AI assistants stay in sync.
- **Public Chat Page**
  - Route: `${canvas_base_url}/chat/${workflow_id}` with open access for any published workflow; OAuth login remains optional based on the publisher’s preference.
  - Frontend loads the shared ChatKit UI bundle (same as Canvas) and exchanges messages with `${backend_url}/api/chatkit` sending `{workflow_id}` with every request.
  - If `require_login=true`, visitors must complete OAuth (e.g., Google) before widget initialization; presence of a valid session cookie becomes mandatory.
  - Backend validates workflow exists, is marked public, and enforces rate limiting (CAPTCHA or other interactive challenges are deferred follow-ups).
  - Page displays workflow name but omits description to keep the experience lightweight.
- **Canvas Chat Bubble**
  - Floating FAB button bottom-right of editor; opens modal containing ChatKit widget.
  - On open, Canvas backend issues short-lived JWT (1–5 min) bound to user + workflow. Modal refreshes token as needed.
  - Requests carry `Authorization: Bearer <jwt>`; backend validates claims and ensures workflow permissions.
  - If user switches to another workflow/tab, fetch new JWT or disable chat until saved.
- **Shared ChatKit Widget**
  - React bundle housed in one module, parameterized by auth mode (JWT vs. public + optional OAuth session).
  - Styling/theme hooks ensure Canvas modal and public page just pass container props.
- **Backend Contract**
- Single `/api/chatkit` endpoint accepts either `Authorization` header (JWT) or a public workflow payload (workflow_id + optional OAuth session cookie).
  - Shared message payload schema, streaming support, error codes for auth failure, unpublished workflow, rate limit, etc.

## Non-functional Requirements
- JWT tokens signed with rotating key material; max TTL 5 minutes.
- Endpoint enforces per-IP/per-workflow rate limits (reusing the existing middleware in `apps/backend/src/orcheo_backend/app/authentication/rate_limit.py`) and exports metrics for Platform Admin abuse monitoring runbooks; CAPTCHA or other challenge flows are future work.
- Logging must redact JWTs.
- CI continues to enforce 95% overall / 100% diff test coverage.
- Chat transcripts (public and Canvas) persist indefinitely unless retention policy changes; stored alongside other ChatKit sessions.

## Success Metrics
- ≥80% of creators who publish a workflow can complete a public chat session without assistance.
- Canvas chat bubble latency ≤ 2s p95 round-trip for first message after open.
- Incidents of unauthorized workflow access = 0 (detected via monitoring of public workflow misuse).

## Rollout & Risks
- Rollout behind feature flag `chatkit_publish_enabled` default off; enable per-workspace.
- Potential risks: open public links being spammed, JWT misconfiguration. Mitigation: unpublish/login toggles, rate limiting, alerting on auth failures.

## Operational Ownership
- Abuse monitoring: Platform Admin team owns dashboards, automated alerts, and policy enforcement for anonymous/public traffic. SRE partners on on-call during rollout and helps build automated mitigations (future CAPTCHA work, rate-limit tuning). Future Trust & Safety hires inherit these responsibilities.
