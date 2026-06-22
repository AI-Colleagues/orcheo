# Studio Chat Bubble Guide

Use this guide to wire the ChatKit-powered chat bubble directly inside the
Studio workflow editor. Unlike the public embedding guide, this flow keeps every
request authenticated with short-lived workflow JWTs so creators can test in
context without leaving Studio.

## When to use this guide
- You are building or modifying the Studio editor (`apps/studio/src/features`)
  and want an in-editor chat experience tied to the workflow on screen.
- You can call the Studio backend (FastAPI) to mint workflow-scoped ChatKit
  sessions via `/api/workflows/{id}/chatkit/session`.
- You need to keep the UX consistent with the floating bubble pattern used
  across workflow nodes rather than embedding a standalone public page.

## Prerequisites
1. **Feature flag** – enable `chatkit_studio_enabled` for the environment (via
   LaunchDarkly, env var, or your preferred flag service). Keep the flag off in
   production until QA is complete.
2. **Authenticated Studio session** – the bubble uses the creator’s bearer
  access token to call `/api/workflows/{id}/chatkit/session`. Anonymous users
  are not supported.
3. **Saved workflow ID** – session issuance fails unless the workflow has been
  saved and has a stable `workflowId`. The hook surfaces the error
  “Save the workflow before opening ChatKit.” when no ID exists (`use-workflow-chat.ts:68-77`).
4. **Backend reachability** – ensure the editor can reach the backend base URL
  returned by `getBackendBaseUrl()` (typically `http://localhost:2025` in dev)
  so JWT refresh calls succeed.
5. **Login (local or hosted)** – authentication is a first-party passwordless
  email IdP. The backend mints and validates HS256 access tokens signed with
  `ORCHEO_AUTH_JWT_SECRET` (sole accepted issuer = `ORCHEO_AUTH_ISSUER`); the
  Studio login screen sends a magic link + OTP. Set `VITE_ORCHEO_AUTH_DISABLED=true`
  to bypass the login gate for local dev.
6. **ChatKit signing key** – set `ORCHEO_CHATKIT_TOKEN_SIGNING_KEY` (HS or RSA
   private key material) so the backend can mint workflow-scoped ChatKit
   session JWTs. Without this key, `/api/workflows/{id}/chatkit/session` returns
   `503 Service Unavailable` with the hint “Set CHATKIT_TOKEN_SIGNING_KEY to
   enable ChatKit session issuance.”

## Component map
| Path | Responsibility |
| --- | --- |
| `apps/studio/src/features/workflow/pages/workflow/hooks/use-workflow-chat.ts` | Manages chat state, issues sessions, and exposes handlers for node interactions. |
| `apps/studio/src/features/chatkit/components/studio-chat-bubble.tsx` | Floating FAB + modal that lazy-loads ChatKit and renders loading/error states. |
| `apps/studio/src/features/chatkit/lib/workflow-session.ts` | Thin client for `/api/workflows/{id}/chatkit/session` (fetch + TTL parsing). |
| `apps/studio/src/features/shared/components/chat-interface-options.ts` | Normalizes `UseChatKitOptions`, header text, greetings, and handler composition. |
| `apps/studio/src/features/workflow/pages/workflow/components/workflow-layout.tsx` | Mounts `StudioChatBubble` and forwards chat props gathered from the hook. |

## Implementation steps

### 1. Initialize workflow chat state
Call `useWorkflowChat` from the Studio workflow controller to obtain everything
the bubble needs: open/close handlers, telemetry hooks, JWT refresh logic, and
node bindings. See `use-workflow-core.ts:131-189` for the reference
implementation.

```ts
const chat = useWorkflowChat({
  nodesRef: history.nodesRef,
  setNodes: history.setNodes,
  workflowId,
  backendBaseUrl: getBackendBaseUrl(),
  userName: user.name,
});
```

The hook:
- Refreshes JWT-backed ChatKit sessions with automatic buffering before expiry
  (`SESSION_REFRESH_BUFFER_MS`, `use-workflow-chat.ts:25-66`).
- Records telemetry for open/close/session events (`use-workflow-chat.ts:129-190`).
- Keeps per-node status in sync so the workflow page reflects running/success states.

### 2. Attach chat triggers to workflow nodes
`useWorkflowChat` exposes `attachChatHandlerToNode`, which injects `onOpenChat`
into every `chatTrigger` node (`use-workflow-chat.ts:247-258`). Run your node
list through this helper before rendering so clicking a chat-capable node opens
the bubble.

```ts
const convertPersistedNodesToStudio = useCallback(
  (persisted: PersistedWorkflowNode[]) =>
    persisted
      .map((node) => toStudioNodeBase(node))
      .map(chat.attachChatHandlerToNode),
  [chat.attachChatHandlerToNode],
);
```

### 3. Render the Studio chat bubble
Mount `StudioChatBubble` near the root of the layout and pass the values returned
from the hook (`workflow-layout.tsx:120-190`).

```tsx
{chat && (
  <StudioChatBubble
    title={chat.chatTitle}
    user={chat.user}
    ai={chat.ai}
    workflowId={chat.workflowId}
    sessionPayload={{
      workflowId: chat.workflowId,
      workflowLabel: chat.chatTitle,
      chatNodeId: chat.activeChatNodeId,
    }}
    backendBaseUrl={chat.backendBaseUrl}
    getClientSecret={chat.getClientSecret}
    sessionStatus={chat.sessionStatus}
    sessionError={chat.sessionError}
    onRetry={chat.refreshSession}
    onResponseStart={chat.handleChatResponseStart}
    onResponseEnd={chat.handleChatResponseEnd}
    onClientTool={chat.handleChatClientTool}
    onDismiss={chat.handleCloseChat}
    onOpen={() => chat.setIsChatOpen(true)}
    isExternallyOpen={chat.isChatOpen}
  />
)}
```

`StudioChatBubble` handles:
- Floating action button + modal chrome, including minimap avoidance logic and
  skeleton placeholders (`studio-chat-bubble.tsx:70-320`).
- Lazy-loading `ChatKitSurface` only after the user opens the panel to keep the
  editor light.
- Displaying loader/error states while JWTs are being created or retried.

### 4. Supply ChatKit options and JWT refresh logic
Inside the bubble, `useChatInterfaceOptions` injects everything required by the
ChatKit SDK (`studio-chat-bubble.tsx:179-209`,
`chat-interface-options.ts:100-173`):
- `getClientSecret` is wired to the hook’s `refreshSession`, which calls
  `requestWorkflowChatSession` on the backend (`workflow-session.ts:35-68`).
- The helper auto-builds the header, optional greeting, composer placeholder,
  and event handlers. You can merge additional `chatkitOptions` (e.g., disabling
  history or customizing the composer) before they reach `ChatKitSurface`.

### 5. Handle advanced interactions (client tools, telemetry, dismissal)
- **Client tools**: The bubble forwards `onClientTool` calls so tools like
  `orcheo.run_workflow` can route through `handleChatClientTool`
  (`use-workflow-chat.ts:192-244`), which hits
  `/api/chatkit/workflows/{workflowId}/trigger` for background runs.
- **Telemetry**: Calls to `recordChatTelemetry` already exist in the hook and
  bubble (`studio-chat-bubble.tsx:147-168`, `use-workflow-chat.ts:129-150`).
  Extend the helper to add new event names rather than inlining analytics.
- **Dismissal state**: Use `onDismiss` to keep other UI (e.g., tutorial panels)
  in sync and prevent orphaned open states.

## Testing & troubleshooting
- **JWT failures (401/403)** – make sure the Studio session cookie is present
  when calling `/api/workflows/{id}/chatkit/session`. In dev, run `make dev-server`
  so both backend and frontend share the same origin.
- **`ChatKit session response missing client secret.`** – indicates backend
  configuration drift; inspect the FastAPI logs for signature errors or missing
  `ORCHEO_AUTH_JWT_SECRET`.
- **Bubble never opens** – confirm `attachChatHandlerToNode` ran on the nodes
  and that `chat.isChatOpen` toggles when clicking the FAB or node CTA.
- **Workflow switch stale state** – the hook resets sessions whenever the
  `workflowId` changes (`use-workflow-chat.ts:50-56`). If you manage IDs
  manually, update the hook inputs accordingly.
- **Minimap overlap** – the bubble automatically offsets itself relative to the
  minimap (`studio-chat-bubble.tsx:26-145`). If you customize the minimap DOM,
  update `MINIMAP_SELECTOR`.

## References
- Component source: `apps/studio/src/features/chatkit/components/studio-chat-bubble.tsx`
- Workflow chat hook: `apps/studio/src/features/workflow/pages/workflow/hooks/use-workflow-chat.ts`
- Session helper: `apps/studio/src/features/chatkit/lib/workflow-session.ts`
- Shared ChatKit options: `apps/studio/src/features/shared/components/chat-interface-options.ts`
- Public embedding reference: `docs/chatkit_integration/webpage_embedding_guide.md`
