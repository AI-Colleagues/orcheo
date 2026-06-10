# Workflow Publishing Guide

Follow this walkthrough to turn any Orcheo workflow into a ChatKit experience.
It covers the CLI/MCP publish flow, highlights the environment variables you
must set, and explains how to hand the resulting share link to the Studio public
page or custom ChatKit embeds.

## When to use this guide
- You have a workflow ready for external testers or end users.
- You want the ChatKit UI (public `.../chat/{workflowId}` page or embedded
  bubble) to execute that workflow.
- You are comfortable running the Orcheo CLI or invoking the mirrored MCP tools.

## Prerequisites
1. **CLI authentication** – run `orcheo login` or set
   `ORCHEO_SERVICE_TOKEN` so the CLI may call your backend.
2. **Backend availability** – start the FastAPI stack (`make dev-server`) and
   confirm it is reachable at the base URL pointed to by `ORCHEO_API_URL`.
3. **CORS allow list** – ensure `ORCHEO_CORS_ALLOW_ORIGINS` includes every
   origin that will load the ChatKit UI (Studio, docs site, local dev server).
   See `docs/environment_variables.md` for syntax.
4. **Domain key** – set `ORCHEO_CHATKIT_DOMAIN_KEY` anywhere the ChatKit JS
   bundle executes (Studio, embeds, or stand-alone demo pages). Local builds may
   default to `domain_pk_localhost_dev`. If you need to generate one, follow
   [Create a ChatKit domain key](webpage_embedding_guide.md#create-a-chatkit-domain-key).
5. **Optional OAuth requirements** – if the workflow should only be available to
   signed-in users, confirm OAuth is configured or the dev-login shim
   (`ORCHEO_AUTH_DEV_LOGIN_ENABLED=true`) is enabled.
6. **Frontend origin override** – when the public ChatKit UI runs on a different
   host/port than your API (`ORCHEO_API_URL`), set
   `ORCHEO_CHATKIT_PUBLIC_BASE_URL` (e.g., `https://canvas.example`) or pass
   `--chatkit-public-base-url` directly to `orcheo workflow publish` so the CLI
   and MCP responses emit the correct `https://.../chat/{workflowId}` links.

## Step 1 – Inspect the workflow
Use the CLI to gather the workflow identifier and ensure it is healthy before
publishing:

```bash
orcheo workflow list
orcheo workflow show wf_123
```

The show command already includes publish metadata (current visibility,
require-login flag, last publish timestamp) so you know whether the workflow is
safe to expose.

## Step 2 – Publish via CLI
Run the publish command from the CLI. The CLI prompts for confirmation unless
you pass `--force`. Add `--require-login` to gate ChatKit behind OAuth:

```bash
orcheo workflow publish wf_123 --require-login
# Override the share URL origin just for this run:
orcheo workflow publish wf_123 --force --chatkit-public-base-url https://canvas.example
```

Behind the scenes the CLI hits `POST /api/workflows/{id}/publish` and prints a
summary:

```
Workflow visibility updated successfully.
Status: Public
Require login: Yes
Published at: 2024-03-22T12:31:00Z
Share URL: https://canvas.example/chat/wf_123
```

## Step 3 – Capture and share the URL
The `Share URL` field is the canonical ChatKit UI entry point. Its origin comes
from `ORCHEO_CHATKIT_PUBLIC_BASE_URL` (or the `--chatkit-public-base-url`
override) when provided; otherwise it strips any trailing `/api` segment from
`ORCHEO_API_URL`. For split local setups (backend on 2025, frontend on 2026),
either export `ORCHEO_CHATKIT_PUBLIC_BASE_URL=http://localhost:2026` or tack on
`--chatkit-public-base-url http://localhost:2026` to the publish command.

- Paste it directly into a browser to load the Studio-hosted public chat page,
  which renders the ChatKit widget bound to the published workflow.
- Record it in product docs or onboarding material so testers can open the chat.
- Feed it into automation scripts that need to validate publish state via
  `orcheo workflow show wf_123`.

If you enable `--require-login`, the page prompts visitors to sign in through
your configured OAuth provider before ChatKit initializes.

## Step 4 – Connect the workflow to other ChatKit surfaces
- **Public webpage embeds** – point any static site at your backend and follow
  `docs/chatkit_integration/webpage_embedding_guide.md` to load ChatKit inside a
  floating bubble. Store the workflow ID or share URL in local state and forward
  it via the `fetchWithWorkflow` helper so every request includes `workflow_id`.
  Be sure the page origin appears in `ORCHEO_CORS_ALLOW_ORIGINS`.
- **Custom chat UIs** – if you want to render your own message list and composer
  instead of embedding the stock widget, follow
  `docs/chatkit_integration/custom_chat_ui_guide.md` to call `/api/chatkit`
  directly from your frontend or mobile client.
- **Studio editor bubble** – internal builders testing unpublished iterations
  can still rely on the Studio bubble described in
  `docs/chatkit_integration/studio_chat_bubble_guide.md`. Publishing is only
  required when you need shareable public access.
- **Automation** – CI or MCP automations may call
  `orcheo workflow publish/unpublish --force` to rotate visibility as part of a
  rollout script. They receive the same share URL format that humans see.

## Step 5 – Maintain publish state
- Run `orcheo workflow unpublish wf_123` (or the MCP equivalent) to revoke the
  link immediately. Existing ChatKit sessions drop once the page reloads.
- Re-run `orcheo workflow publish wf_123 --no-require-login` to remove an OAuth
  requirement without changing the share URL.
- Use `orcheo workflow list --include-archived` to audit everything that is
  currently public.

## Troubleshooting
- **403 from `/chat` page** – the workflow was unpublished or the ID is wrong.
  Re-run `orcheo workflow show wf_123` to confirm `is_public=True`.
- **401 from embeds** – the workflow requires login but the page is served from
  a domain without the OAuth cookies. Host the page under the same origin or use
  the login-required Studio route.
- **CORS or preflight failures** – update `ORCHEO_CORS_ALLOW_ORIGINS` with every
  `http(s)://host:port` that will load ChatKit, restart the backend, and refresh.
- **Domain key errors** – supply `ORCHEO_CHATKIT_DOMAIN_KEY` (or
  `window.ORCHEO_CHATKIT_DOMAIN_KEY` in the browser) so the SDK can validate
  the request origin.
- **CLI refuses to publish while offline** – the command enforces network
  access because it must call the backend; drop `--offline` and retry.

## References
- CLI implementation:
  `packages/sdk/src/orcheo_sdk/cli/workflow/commands/publishing.py`
- Share URL helpers:
  `packages/sdk/src/orcheo_sdk/services/workflows/publish.py`
- Backend publish routes:
  `apps/backend/src/orcheo_backend/app/routers/workflows.py`
- ChatKit embedding reference:
  `docs/chatkit_integration/webpage_embedding_guide.md`
