# Design Document

## For ChatKit Widget Support for Orcheo Workflows

- **Version:** 0.1
- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2025-12-15
- **Status:** Approved

---

## Overview

This design enables Orcheo workflows to surface ChatKit widgets (Card/ListView roots) in the ChatKit UI and to receive user widget actions. Today the backend streams only assistant messages; widget roots emitted by workflows are ignored, and widget actions are not routed to workflows. The goal is to serialize widget outputs, rehydrate the already-persisted LangChain ToolMessages as `WidgetItem`s, render them in ChatKit React surfaces, and handle widget actions end-to-end with existing auth and repository constraints. Widget artifacts are authored via the ChatKit Widget Builder (https://widgets.chatkit.studio/) or equivalent template files.

## Components

- **Workflow Runtime (orcheo.graph, AgentNode + MCP widget tool)**
  - Emits widget roots in ToolMessage artifacts/content; may stream widget updates.
  - Depends on credential resolution and LangGraph checkpointer.
- **ChatKit Server (apps/backend/.../chatkit/server.py)**
  - Converts workflow outputs into ChatKit thread items, streams events, and handles custom actions.
  - Depends on WorkflowRepository, Vault, and ChatKit store.
- **ChatKit Store (SQLite/in-memory)**
  - Persists threads, messages, widget items, and attachments; surfaces history pagination.
- **ChatKit React Client (Studio/public surfaces)**
  - Renders widget items and forwards widget actions via `widgets.onAction`/`sendAction`.
  - Depends on ChatKit JS SDK and existing header/composer plumbing.
- **Action Router (new in server)**
  - Translates widget `Action` payloads into workflow invocations with contextual metadata.

## Request Flows

### Flow 1: Workflow emits a widget

1. User sends a message via ChatKit UI; ChatKit server builds inputs and invokes workflow.
2. Workflow executes AgentNode with MCP widget tool, producing `WidgetRoot` plus reply text.
3. Server scans LangChain ToolMessages for widget payloads, hydrates them into `WidgetItem` thread entries, and streams `ThreadItemDoneEvent` to the client along with assistant message. ToolMessages remain the single source of truth for widgets.
4. Client renders widget in thread history; history fetch returns widget items for future sessions.

### Flow 2: User triggers a widget action

1. User interacts with widget (e.g., submits form); ChatKit JS fires `widgets.onAction`.
2. Client calls `control.sendAction(action, widgetItem.id)` to backend.
3. Server implements `action()` to translate `Action` payload into workflow inputs (including thread/workflow metadata) and re-invokes workflow.
4. New assistant/widget items are streamed (persistence is already handled via ToolMessages); UI updates thread history accordingly.

### Flow 3: Streaming widget updates (future)

1. Workflow streams widget deltas (`WidgetRootUpdated`/`WidgetComponentUpdated`/`WidgetStreamingTextValueDelta`) during long-running tasks.
2. Server forwards update events to ChatKit; client renders incremental UI changes without replacing the entire widget.
3. This flow is deferred past the MVP; implement once basic widget rendering/actions are stable.

## API Contracts

```
POST /api/chatkit (SSE/WebSocket negotiated by ChatKit SDK)
Headers:
  Authorization: Bearer <chatkit session or JWT> (when required)
  X-ChatKit-Domain-Key: <domain key> (public flows)
Payload:
  ChatKitReq (threads.create, threads.add_user_message, threads.custom_action, etc.)

Streaming Responses:
  ThreadStreamEvent union:
    - thread.item.done (AssistantMessageItem | WidgetItem | ...)
    - widget.root.updated / widget.component.updated / widget.streaming_text.value_delta (optional)
    - error with allow_retry flag on failure
```

Widget items serialized via `chatkit.types.WidgetItem` with `widget: WidgetRoot`. Actions use `ThreadsCustomActionReq` with `Action` payload dispatched from client via `sendAction`.

## Data Models / Schemas

| Field | Type | Description |
|-------|------|-------------|
| widget | WidgetRoot | Renderable widget tree (Card/ListView) |
| copy_text | string? | Optional copy text for the widget item |
| action payload | Action[str, Any] | Declarative action from widget components |

Workflow output contract (server expectation):

```python
ToolMessage(
    name="widget_tool",
    tool_call_id="call_123",
    content=[{"type": "text", "text": "<widget root json>"}],
    artifact={
        "structured_content": {
            "type": "Card",
            "children": [...],
            "confirm": {...},
        }
    },
)
```

Server hydrates widget payloads from ToolMessages (prefer `artifact["structured_content"]`, fallback to text) into `WidgetItem`s.

## Security Considerations

- Reuse existing ChatKit auth (public vs. JWT) and workflow access checks.
- Validate widget payloads (size, schema via `WidgetRoot` Pydantic) to prevent injection/overload.
- Preserve thread metadata and workflow_id; deny actions if workflow_id is missing or invalid.
- Rate limits already applied at ChatKit gateway; ensure widget action path respects them.
- Vault access only via WorkflowExecutor; no secrets should enter widget payloads.

## Performance Considerations

- Widget payload size caps and rejection with user-facing notice to avoid large SSE frames.
- Rely on existing SQLite serialization paths (via ToolMessages) for widget persistence; avoid over-emitting updates.
- Streaming updates should be throttled to prevent UI flooding; rely on ChatKit SDK buffering.
- Backpressure: leverage ChatKit stream cancel handling to drop pending widget emissions on cancel.

## Testing Strategy

- **Unit tests:** serialization of WidgetRoot → WidgetItem; action handler input mapping; error handling for invalid widgets.
- **Integration tests:** end-to-end ChatKit server flow that surfaces widget items (already persisted via ToolMessages) in history; action round-trip producing new items.
- **Manual QA:** sample `examples/chatkit_widgets` workflow in dev/staging; public and JWT flows; Studio bubble integration.

## Rollout Plan

1. Phase 1: Dev enablement with sample workflow; validate UI rendering and action handling.
2. Phase 2: Staging enablement for public page and Studio bubble; monitor widget error logs.
3. Phase 3: Production rollout after tests pass; rely on logging and deploy rollback for quick disable.

Logging and size limits guard against regressions; no schema migrations beyond store reuse.

## Open Issues

- [ ] Define size limits and rejection UX for oversized widget payloads.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2025-12-15 | Shaojie Jiang | Initial draft |
