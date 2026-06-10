# ChatKit Integration Plan

Author: Shaojie Jiang. See `./requirements.md` and `./design.md` for full context.

## Milestone 0 – Backend foundations ✅ *(complete)*

All backend prerequisites for ChatKit sharing are now in place. The workflow domain
model understands publish metadata, repository layers persist the new state, and the
ChatKit router accepts either workflow JWTs or public workflows (optionally gated by OAuth)
with consistent rate-limiting and error handling.

1. **Publish metadata**
   - [x] Extended the workflow model with publish state (`is_public`,
     `published_at`, `published_by`, `require_login`) and helper utilities for
     tracking OAuth requirements.
   - [x] Added persistence updates and tests covering publish/revoke +
     `require_login` transitions across in-memory and SQLite repositories.
2. **Publish management APIs**
   - [x] Implemented `POST /api/workflows/{id}/publish` and `/publish/revoke`
     endpoints with responses that surface current publish status.
   - [x] Ensured the payload accepts the `require_login` flag and audit logging
     hooks are wired in for future observability.
3. **ChatKit endpoint auth**
   - [x] Updated `/api/chatkit` to validate either workflow-scoped JWTs
     (`Authorization: Bearer …`) or published workflows, enforcing JWT checks,
     OAuth session validation when `require_login` is set, and rate limiting via
     the shared middleware.
   - [x] Persist chat transcripts through the existing session store for both auth
     modes and added backend tests (including
     `tests/backend/test_chatkit_authentication.py`) that cover the new helper
     flows.

## Milestone 1 – CLI and MCP publish UX
_Studio-side publish surfaces remain future work; this milestone delivers the CLI flows plus matching MCP tools so we can unblock external testing and AI-assistant workflows before Studio parity ships._
- [x] **Publish command**
  - [x] Implement `orcheo workflow publish <workflow_id>` with a `--require-login` option (default off), confirmation prompts, and summary output showing the share URL once.
  - [x] Handle errors (missing workflow, permission denied) with actionable hints and non-zero exit codes.
  - [x] Expose the same flow via `orcheo-mcp` (e.g., `workflows.publish`) so Claude/Codex automations receive identical responses, including require-login flag handling.
- [x] **Unpublish command**
  - [x] Add `orcheo workflow unpublish <workflow_id>` that calls the revoke API and updates local cache/state.
  - [x] Mirror the action in MCP (`workflows.unpublish`) so assistants receive identical status payloads and error semantics.
- [x] **Status surfacing**
  - [x] Update `orcheo workflow list` and `orcheo workflow show` to include publish status (`public/private`, `require_login` flag, share URL if available), and return the same enriched metadata from the MCP `list_workflows`/`show_workflow` tools.
  - [x] Write CLI + MCP regression tests covering each command/tool path and add docs/examples so users (and assistants) can script the flows consistently.

## Milestone 2 – Public chat page ([reference template](https://github.com/openai/openai-chatkit-advanced-samples/tree/main/frontend))
- [x] **Route + bootstrapping**
  - [x] Add `${studio_base_url}/chat/:workflowId` page; sessions should open automatically once metadata loads (no publish tokens required).
  - [x] Fetch workflow metadata to display the workflow name only (no description).
  - [x] Initialize shared ChatKit widget with public auth mode and optionally prompt for OAuth login before mounting when `require_login=true`.
- [x] **Hardening & UX**
  - [x] Handle unauthorized/expired sessions with friendly error screens and CTA to contact owner.
  - [x] Add basic rate-limit feedback and loading skeletons; CAPTCHA defenses will be tracked as follow-up work.
  - [x] Ensure OAuth sessions use secure HttpOnly cookies and display clear login prompts when required.

## Milestone 3 – Studio chat bubble
- [x] **JWT session issuance**
  - [x] Add `POST /api/workflows/{id}/chatkit/session` to return 5-min JWTs tied to workflow + user.
  - [x] Cover with unit tests ensuring permission checks.
- [x] **UI components**
  - [x] Create floating FAB + modal in `apps/studio/src` that lazy-loads the ChatKit widget.
  - [x] Integrate token refresh logic, workflow switch handling, loading/error states.
  - [x] Add telemetry for open/close events and request failures.
- [x] **Shared widget refactor**
  - [x] Move existing ChatKit client logic into a reusable module (`features/chatkit`).
  - [x] Deduplicate code paths so Studio modal and public page import the same component.

## Milestone 4 – QA, docs, rollout
- [x] **Testing matrix**
  - [x] Expand backend + frontend test suites (unit + integration) covering both auth modes.
  - [x] Document manual QA checklist referenced in requirements success metrics, including OAuth-required flows and transcript persistence checks.
- [x] **Docs & enablement**
  - [x] Update product docs/tutorials explaining how to publish, share links, and use Studio bubble.
    - [x] Add Studio chat bubble integration guide (`../../../docs/chatkit_integration/studio_chat_bubble_guide.md`).
    - [x] Add workflow publishing guide for ChatKit UI (`../../../docs/chatkit_integration/workflow_publish_guide.md`).
  - [x] Ship feature flags (`chatkit_studio_enabled`, `chatkit_publish_enabled`) and rollout plan.
  - [x] Monitor logs/metrics post-deploy and prepare rollback steps.
