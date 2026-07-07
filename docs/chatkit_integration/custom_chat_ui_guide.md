# Custom Chat UI Guide

Use this guide when you want to build your own chat surface on top of Orcheo instead of embedding the stock ChatKit widget. The integration pattern uses the same raw `/api/chatkit` contract that Orcheo's hosted ChatKit surfaces use underneath.

## When to use this guide

- You are building a native-feeling chat UI in React, React Native, WeChat Mini Program, Flutter, Swift, or another frontend stack.
- You want Orcheo to manage workflow execution, thread history, and assistant responses, but you want full control over rendering.
- You do not want to ship `<openai-chatkit>` or the hosted ChatKit web bundle.

If you want the stock ChatKit UI instead, use [Webpage Embedding](webpage_embedding_guide.md) or [Studio Chat Bubble](studio_chat_bubble_guide.md).

## Integration model

Your UI owns the presentation layer:

- local message list
- composer state
- typing/loading indicators
- scroll behavior
- mobile layout quirks

Orcheo owns the conversation backend:

- thread creation and storage
- workflow dispatch
- assistant response generation
- SSE event stream
- optional attachment storage

The core flow is:

1. Keep a local `threadId` in UI state.
2. On the first user turn, `POST /api/chatkit` with `type: "threads.create"`.
3. On later turns, `POST /api/chatkit` with `type: "threads.add_user_message"` and the existing `thread_id`.
4. Parse the response, save the returned thread ID, and update the last assistant message from SSE events.

## Authentication options

Pick one of these before wiring the UI:

| Mode | When to use it | What the client sends |
| --- | --- | --- |
| Published workflow | Public or semi-public chat surface | `workflow_id` in the JSON payload |
| Published + login required | Same-origin web UI with Orcheo OAuth cookies | `workflow_id` plus browser cookies |
| Session JWT | Private or third-party custom UI | `Authorization: Bearer <client_secret>` plus `workflow_id` |

Notes:

- `/api/chatkit` always requires a top-level `workflow_id`.
- For published access, Orcheo checks that the workflow is public and optionally that an OAuth session exists.
- For JWT access, mint a short-lived token through `POST /api/chatkit/session` on your server side. See the JWT section in [Webpage Embedding](webpage_embedding_guide.md#embedding-on-a-third-party-website-with-jwt-tokens).
- The workflow-scoped endpoint `POST /api/workflows/{workflow_id}/chatkit/session` is mainly for authenticated first-party Studio flows.

## Request shape

Custom implementations send ChatKit-style request bodies directly to `/api/chatkit`.

### First turn: create a thread

```json
{
  "type": "threads.create",
  "params": {
    "input": {
      "content": [
        {
          "type": "input_text",
          "text": "Hello"
        }
      ],
      "attachments": [],
      "quoted_text": null,
      "inference_options": {}
    }
  },
  "metadata": {
    "workflow_id": "workflow_uuid",
    "workflow_name": "Orcheo Bot"
  },
  "workflow_id": "workflow_uuid"
}
```

### Later turns: append to the existing thread

```json
{
  "type": "threads.add_user_message",
  "params": {
    "thread_id": "thr_123",
    "input": {
      "content": [
        {
          "type": "input_text",
          "text": "Can you summarize that?"
        }
      ],
      "attachments": [],
      "quoted_text": null,
      "inference_options": {}
    }
  },
  "metadata": {
    "workflow_id": "workflow_uuid",
    "workflow_name": "Orcheo Bot"
  },
  "workflow_id": "workflow_uuid"
}
```

Recommendations:

- Keep `workflow_id` at the top level. Orcheo validates that field before processing the request.
- Reuse the same `thread_id` for the conversation. Do not replay your full message history on every turn.
- Keep `attachments`, `quoted_text`, and `inference_options` in the payload shape even if they are empty. It makes upgrades easier.
- Include `workflow_name` in metadata for easier debugging and thread inspection.

## Minimal client implementation

This TypeScript helper shows the smallest useful client abstraction for a custom UI: build the request payload, post it to `/api/chatkit`, then parse the returned events. The code stays UI-framework-agnostic so you can drop it into React, React Native, Flutter, or another frontend stack.

```ts
type ChatRole = "user" | "assistant";

type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
};

type ChatConfig = {
  baseUrl: string;
  workflowId: string;
  workflowName: string;
  bearerToken?: string;
};

function buildUserInput(text: string) {
  return {
    content: [{ type: "input_text", text }],
    attachments: [],
    quoted_text: null,
    inference_options: {},
  };
}

function buildChatKitPayload(
  config: ChatConfig,
  userMessage: string,
  threadId?: string,
) {
  const metadata = {
    workflow_id: config.workflowId,
    workflow_name: config.workflowName,
  };

  if (threadId) {
    return {
      type: "threads.add_user_message",
      params: {
        thread_id: threadId,
        input: buildUserInput(userMessage),
      },
      metadata,
      workflow_id: config.workflowId,
    };
  }

  return {
    type: "threads.create",
    params: {
      input: buildUserInput(userMessage),
    },
    metadata,
    workflow_id: config.workflowId,
  };
}

async function sendChatTurn(
  config: ChatConfig,
  userMessage: string,
  threadId?: string,
) {
  const response = await fetch(`${config.baseUrl}/api/chatkit`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(config.bearerToken
        ? { Authorization: `Bearer ${config.bearerToken}` }
        : {}),
    },
    body: JSON.stringify(buildChatKitPayload(config, userMessage, threadId)),
  });

  const rawBody = await response.text();
  if (!response.ok) {
    throw new Error(rawBody || `Chat request failed with ${response.status}`);
  }

  return parseChatKitSse(rawBody, threadId);
}
```

## Parsing the SSE response

`/api/chatkit` may return `text/event-stream`. If your runtime gives you the full response body at once, you can parse it as a string, split on lines, and extract only the event types your UI cares about.

These are the most important event types for a custom UI:

- `thread.created`: save `event.thread.id` as your local `threadId`
- `thread.item.updated`: append streaming assistant text as it arrives
- `thread.item.done`: capture the final assistant message content and use `event.item.thread_id` as a fallback thread identifier

Example parser:

```ts
function parseChatKitSse(
  data: string,
  previousThreadId = "",
): {
  threadId: string;
  responseText: string;
} {
  let threadId = previousThreadId;
  let streamedText = "";
  let finalText = "";

  for (const line of data.split("\n")) {
    if (!line.startsWith("data: ")) {
      continue;
    }

    try {
      const event = JSON.parse(line.slice(6));

      if (event.type === "thread.created" && event.thread?.id) {
        threadId = event.thread.id;
      }

      if (!threadId && event.item?.thread_id) {
        threadId = event.item.thread_id;
      }

      if (
        event.type === "thread.item.updated" &&
        event.update?.type === "content_part_added" &&
        event.update.part?.text
      ) {
        streamedText += event.update.part.text;
      }

      if (
        event.type === "thread.item.done" &&
        event.item?.type === "assistant_message"
      ) {
        let completedText = "";
        for (const part of event.item.content ?? []) {
          if (part.type === "output_text" && part.text) {
            completedText += part.text;
          }
        }
        if (completedText) {
          finalText = completedText;
        }
      }
    } catch {
      // Ignore malformed or forward-incompatible event lines.
    }
  }

  return { threadId, responseText: finalText || streamedText };
}
```

Implementation notes:

- Some environments buffer the whole SSE response before returning it. That still works; you just lose token-by-token rendering.
- In browsers that support streaming `fetch()`, you can read the response incrementally and apply the same event parsing logic chunk by chunk.
- Preserve the prior `threadId` when a follow-up turn does not emit `thread.created`. Some responses only expose the thread on `event.item.thread_id`.
- Ignore event types you do not recognize. Orcheo may emit progress or tool-related events that your UI does not need to render.

## Suggested UI state model

A simple and reliable state update pattern is:

1. Push the user message into local state immediately.
2. Push an empty assistant placeholder message before the request starts.
3. Disable the composer while the request is in flight.
4. Replace the last assistant message as SSE text arrives or when the final event lands.
5. Store the returned `threadId` for the next turn.

That pattern avoids message duplication and makes retries straightforward.

## Attachments

If your custom UI needs uploads, use `POST /api/chatkit/upload` with multipart form data:

- field name: `file`
- response: attachment metadata including `id`, `name`, `mime_type`, `type`, and `storage_path`

Attach the returned object to `input.attachments` in your next `/api/chatkit` request. Orcheo stores the file and lets downstream workflow nodes consume it later.

## Troubleshooting

- `400 workflow_id is required.`: include a top-level `workflow_id` in every `/api/chatkit` request.
- `400 workflow_id must be a valid UUID.`: use the workflow UUID, not the public `/chat/...` URL segment unless it is already the UUID.
- `403 Publish authentication failed: workflow is not published.`: publish the workflow first or switch to JWT-backed sessions.
- `401 Publish authentication failed: OAuth login is required to access this workflow.`: your workflow was published with `require_login=true`, but the UI is not sending the expected session cookies.
- `401 ChatKit session token authentication failed`: the bearer token is missing, expired, or for a different workflow.
- Empty assistant replies: make sure your parser handles both `thread.item.updated` and `thread.item.done`.
- Duplicated assistant text: do not blindly append both streaming deltas and the final message unless your parser deduplicates.

## References

- Hosted ChatKit request wrapper: `apps/studio/src/features/chatkit/lib/chatkit-client.ts`
- Hosted ChatKit widget example: `colleague-candidates/examples/chatkit-embedding.html`
- Publish flow: [Workflow Publishing](workflow_publish_guide.md)
- Hosted widget flow: [Webpage Embedding](webpage_embedding_guide.md)
- Studio-authenticated flow: [Studio Chat Bubble](studio_chat_bubble_guide.md)
