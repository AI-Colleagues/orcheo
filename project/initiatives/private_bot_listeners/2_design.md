# Design Document

## For Private Bot Listener Nodes

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-11
- **Status:** Approved

---

## Overview

This design adds a listener plane to Orcheo so workflows can react to Telegram, Discord, and QQ bot messages without exposing the backend through a public inbound URL. Workflow authors still configure listener nodes in the graph, but a dedicated runtime supervisor owns the long-lived polling loops and gateway sessions needed to keep those bots connected.

The existing execution model remains intact. Listener nodes do not execute user logic directly; instead, they compile into subscription records. A supervisor process starts platform adapters for active subscriptions, receives external bot events, normalizes them into Orcheo trigger inputs, and enqueues standard workflow runs. Telegram uses Bot API long polling. Discord uses Gateway WebSocket sessions. QQ uses Tencent's current API v2 AppID/clientSecret AccessToken flow, the documented `GET /gateway` or `GET /gateway/bot` session bootstrap, and the QQ Bot WebSocket session pattern demonstrated by the maintained OpenClaw plugin.

Studio template workflows are part of the design deliverable, not only documentation. The implementation must prove two graph patterns in real templates: one template per listener platform where that listener feeds an `AgentNode` that generates the reply text, and one template where Telegram, Discord, and QQ listeners in parallel share the same `AgentNode` and downstream reply logic. A template is only considered complete if its reply path uses supported workflow nodes end to end. If a required send node is missing for Discord or QQ, adding that minimal outbound reply node remains part of this initiative.

External validation snapshot taken on 2026-03-11:

- Tencent's official QQ docs still publish listener-relevant material under `/wiki/develop/api-v2/`.
- The API v2 auth docs define `https://bots.qq.com/app/getAppAccessToken`, a default 7200-second token lifetime, and a 60-second overlap window when a token is refreshed near expiry.
- The Gateway docs define heartbeat, invalid-session, resume, and `session_start_limit` behavior and show sandbox Gateway URLs under `wss://sandbox.api.sgroup.qq.com/websocket`.
- `tencent-connect/openclaw-qqbot` and `tencent-connect/botgo` were both live GitHub repositories, not archived, when checked for this draft.

## Components

- **Listener Nodes (Orcheo SDK / Studio)**
  - `TelegramBotListenerNode`, `DiscordBotListenerNode`, and `QQBotListenerNode`.
  - Responsibility: declare platform-specific listener configuration inside a workflow graph.
  - Key dependencies: credential references, filter config, workflow activation pipeline.

- **Workflow Activation Compiler**
  - Extracts listener node config from an activated workflow version.
  - Creates, updates, or deactivates listener subscription records.
  - Prevents invalid combinations such as Telegram polling plus Telegram webhook on the same bot token when configured in the same workspace.

- **Listener Subscription Repository**
  - Persists active listener definitions, health status, cursors, dedupe windows, and runtime ownership.
  - Enables restart recovery and operational inspection.

- **Listener Supervisor**
  - Long-lived process that watches active subscriptions and owns adapter lifecycles.
  - Starts, stops, and restarts platform adapters.
  - Reports health and backoff state.

- **Platform Adapters**
  - **Telegram Polling Adapter**
    - Calls `getUpdates` with the last committed offset.
    - Converts updates into normalized listener events.
  - **Discord Gateway Adapter**
    - Fetches `GET /gateway/bot`, opens WSS connection, sends IDENTIFY or RESUME, maintains heartbeat, and converts dispatch events into normalized listener events.
  - **QQ Gateway Adapter**
    - Exchanges `AppID` and `clientSecret` for an API v2 access token.
    - Refreshes tokens before expiry, using the documented 60-second overlap window so in-flight requests do not fail during rotation.
    - Calls `GET /gateway` or `GET /gateway/bot`, opens WSS connection, respects `session_start_limit`, maintains heartbeat, persists `sessionId` plus sequence, and converts QQ events into normalized listener events.

- **Listener Dispatcher**
  - Persists cursor/session progress after safe checkpoints.
  - Applies dedupe.
  - Enqueues workflow runs through the existing run repository and worker queue.

- **Existing Workflow Runtime**
  - Executes the dispatched workflow run.
  - Reuses existing platform send nodes where possible, such as `MessageTelegramNode`.
  - If a platform template needs a reply node that does not yet exist, the initiative must add a minimal first-class outbound node for that platform rather than rely on placeholder manual API calls.

- **Studio Templates**
  - One template each for Telegram, Discord, and QQ listener workflows with an `AgentNode` producing reply content.
  - One template with Telegram, Discord, and QQ listeners in parallel feeding a shared `AgentNode`.
  - Responsibility: act as the product-level validation artifact for listener node usability, shared-downstream execution semantics, and reply routing.
  - Constraint: templates cannot depend on undocumented custom code or generic stopgap HTTP steps for reply delivery; they must use existing or newly delivered reply nodes.
  - Lifecycle rule: templates must ship with `template_version`, `min_orcheo_version`, `validated_provider_api`, and a named owner so provider changes trigger explicit revalidation.

## Request Flows

### Flow 1: Activate a workflow with a Telegram listener

1. A user activates a workflow version containing `TelegramBotListenerNode`.
2. The activation compiler resolves credential references and writes a `listener_subscription` record.
3. The listener supervisor notices the new active subscription.
4. The Telegram adapter starts a long-poll loop with the configured token, `allowed_updates`, and timeout.
5. Each Telegram update is normalized and dispatched as a workflow run.
6. After successful handoff, the adapter commits the next Telegram offset.

### Flow 2: Receive a Telegram message

1. Telegram returns one or more updates from `getUpdates`.
2. The adapter filters unwanted update types and chat types.
3. The adapter emits a normalized payload such as:
   ```json
   {
     "platform": "telegram",
     "event_type": "message",
     "dedupe_key": "telegram:123456789",
     "bot_identity": "telegram_bot_a",
     "message": {
       "chat_id": "12345",
       "user_id": "67890",
       "text": "hello"
     },
     "raw_event": {}
   }
   ```
4. The dispatcher creates a workflow run with `triggered_by="listener"`.
5. The normal worker executes downstream nodes, which may include `MessageTelegramNode` or AI nodes.

### Flow 3: Activate a workflow with a Discord listener

1. A user activates a workflow version containing `DiscordBotListenerNode`.
2. The activation compiler writes a subscription record with token reference, intents, event filters, and optional shard info.
3. The supervisor starts a Discord Gateway adapter for that bot identity.
4. The adapter calls `GET /gateway/bot`, opens the returned WSS URL, receives `Hello`, schedules heartbeats, and sends `IDENTIFY`.
5. The adapter processes Gateway dispatch events and forwards matching ones to the dispatcher.

### Flow 4: Receive a Discord message event

1. Discord sends a `MESSAGE_CREATE` dispatch event over the Gateway session.
2. The adapter verifies that the event matches configured guild/channel/DM filters.
3. The adapter derives a dedupe key from message ID and dispatch metadata.
4. The dispatcher enqueues a workflow run with normalized Discord inputs.
5. On disconnect, the adapter uses `resume_gateway_url` plus sequence state when possible; otherwise it reconnects and re-identifies.

### Flow 5: Restart recovery

1. The supervisor starts and claims active subscriptions.
2. Telegram adapters resume from the persisted offset.
3. Discord adapters resume from the persisted `session_id`, sequence number, and `resume_gateway_url` when valid.
4. QQ adapters resume from the persisted session ID and sequence number when they are still valid for the configured AppID.
5. Dedupe state suppresses near-term duplicates caused by reconnect overlap.

### Flow 6: Activate a workflow with a QQ listener

1. A user activates a workflow version containing `QQBotListenerNode`.
2. The activation compiler writes a subscription record with QQ account credentials, event filters, and account identity.
3. The supervisor starts a QQ Gateway adapter for that account.
4. The adapter exchanges `AppID` and `clientSecret` for an access token.
5. The adapter calls the QQ Bot `/gateway` or `/gateway/bot` API, records returned session-start limits, opens the returned WebSocket URL, starts heartbeats, and begins receiving events.
6. Matching events are normalized and forwarded to the dispatcher.

### Flow 7: Receive a QQ message event

1. QQ sends a C2C, group, or channel message event over the Gateway session.
2. The adapter verifies that the event matches configured scene filters.
3. The adapter derives a dedupe key from provider event identifiers.
4. The dispatcher enqueues a workflow run with normalized QQ inputs.
5. Outbound replies use the same QQ account identity that received the event.

### Flow 8: Run a shared-agent workflow from three listeners

1. A workflow version is activated with `TelegramBotListenerNode`, `DiscordBotListenerNode`, and `QQBotListenerNode` wired to the same downstream `AgentNode`.
2. The activation compiler emits one subscription per listener node, each with its own platform config, bot identity, and cursor/session state.
3. Any of the three platform adapters may independently dispatch a workflow run for the same workflow version.
4. The runtime receives normalized listener input plus listener metadata such as `platform`, `listener_subscription_id`, and `bot_identity`.
5. The shared `AgentNode` decides the reply text without needing platform-specific branching for core reasoning.
6. Downstream reply nodes or routing logic use listener metadata to send the reply through the matching Telegram bot, Discord bot, or QQ account.
7. If the required platform reply node does not exist yet, that missing node is treated as a blocking implementation gap for template completion and must be added before the template is accepted.

## API Contracts

No new public ingress endpoint is required for Telegram, Discord, or QQ listener operation. The feature only adds internal contracts between workflow activation, listener supervision, and run dispatch.

### Listener Subscription Record

```json
{
  "id": "sub_123",
  "workspace_id": "ws_1",
  "workflow_id": "wf_1",
  "workflow_version_id": "wv_5",
  "node_name": "telegram_listener",
  "platform": "telegram",
  "status": "active",
  "credential_ref": "[[telegram_token_a]]",
  "config": {
    "allowed_updates": ["message"],
    "allowed_chat_types": ["private"],
    "poll_timeout_seconds": 30
  }
}
```

### Normalized Dispatch Payload

```json
{
  "platform": "discord",
  "event_type": "MESSAGE_CREATE",
  "dedupe_key": "discord:message:141234567890",
  "listener_subscription_id": "sub_456",
  "bot_identity": "discord_bot_support",
  "message": {
    "guild_id": "111",
    "channel_id": "222",
    "message_id": "333",
    "user_id": "444",
    "content": "hello"
  },
  "raw_event": {}
}
```

The normalized payload is the contract that allows multiple listener nodes to share downstream workflow logic. Templates and production workflows should rely on `platform`, `listener_subscription_id`, and `bot_identity` to decide reply transport while keeping the core response-generation path shared.

### Missing Send Node Policy

- Telegram should reuse `MessageTelegramNode` unless a concrete incompatibility is found.
- Discord and QQ should reuse existing outbound nodes if they already satisfy listener-reply needs.
- If Discord or QQ does not have a suitable outbound node, the implementation must add a minimal supported reply node for that platform as part of this initiative.
- The canonical reply-node names for this initiative are `MessageTelegramNode`, `MessageDiscordNode`, and `MessageQQNode`.
- Templates are not allowed to close this gap by requiring raw HTTP nodes, ad hoc code nodes, or manual post-processing outside the workflow graph.

### Template Acceptance Contract

- A template passes acceptance only if it imports cleanly into Studio, validates under the current schema, and runs end to end without manual node rewiring.
- Single-platform templates must prove `listener -> AgentNode -> provider reply node`.
- The shared template must prove that Telegram, Discord, and QQ listener events can all enter the same downstream `AgentNode` while preserving correct reply transport and bot identity.
- Every accepted template must record `template_version`, `min_orcheo_version`, `validated_provider_api`, and a validation date.
- Template major versions increase when the workflow shape or provider API contract changes. Validation-only refreshes increase the minor version.

### Internal Dispatch Call

```
dispatch_listener_event(subscription_id, payload) -> run_id

Behavior:
  - validate subscription is active
  - check dedupe window
  - create workflow run
  - enqueue worker execution
  - return run identifier
```

## Data Models / Schemas

### listener_subscription

| Field | Type | Description |
|---|---|---|
| id | string | Unique subscription identifier |
| workspace_id | string | Owning workspace |
| workflow_id | string | Workflow receiving events |
| workflow_version_id | string | Immutable workflow version reference |
| node_name | string | Listener node name in the graph |
| platform | string | `telegram`, `discord`, or `qq` |
| bot_identity_key | string | Stable key derived from credential reference and platform |
| config_json | json | Serialized listener config |
| status | string | `active`, `paused`, `error`, `disabled` |
| assigned_runtime | string | Supervisor instance currently owning the subscription |
| last_event_at | datetime | Last successful event receipt time |
| last_error | string | Most recent adapter error summary |

### listener_cursor

| Field | Type | Description |
|---|---|---|
| subscription_id | string | Foreign key to listener subscription |
| telegram_offset | bigint | Next `getUpdates` offset to request |
| discord_session_id | string | Session ID for Gateway resume |
| discord_sequence | bigint | Last acknowledged Gateway sequence |
| discord_resume_url | string | Resume URL from Ready event |
| qq_session_id | string | Session ID for QQ Gateway resume |
| qq_sequence | bigint | Last acknowledged QQ Gateway sequence |
| qq_app_id | string | AppID used when the QQ session was persisted |
| updated_at | datetime | Cursor update timestamp |

### listener_dedupe

| Field | Type | Description |
|---|---|---|
| subscription_id | string | Foreign key to listener subscription |
| dedupe_key | string | Provider-specific unique event key |
| expires_at | datetime | TTL boundary for duplicate suppression |

### Node Config Sketches

```json
{
  "type": "TelegramBotListenerNode",
  "name": "telegram_listener",
  "token": "[[telegram_token_a]]",
  "allowed_updates": ["message", "callback_query"],
  "allowed_chat_types": ["private"],
  "poll_timeout_seconds": 30
}
```

### Template Workflow Sketches

Single-listener templates should follow a pattern equivalent to:

```
TelegramBotListenerNode -> AgentNode -> MessageTelegramNode
DiscordBotListenerNode -> AgentNode -> MessageDiscordNode
QQBotListenerNode -> AgentNode -> MessageQQNode
```

The shared template should follow a pattern equivalent to:

```
TelegramBotListenerNode --\
DiscordBotListenerNode ----> AgentNode -> platform-aware reply routing -> MessageTelegramNode | MessageDiscordNode | MessageQQNode
QQBotListenerNode -------/
```

The purpose of these templates is to force implementation clarity around how multiple listener nodes enter a shared downstream path in Studio and runtime execution. Missing reply nodes are part of that clarity requirement, not an exception to it.

```json
{
  "type": "DiscordBotListenerNode",
  "name": "discord_listener",
  "bot_token": "[[discord_bot_support]]",
  "intents": ["DIRECT_MESSAGES", "GUILD_MESSAGES", "MESSAGE_CONTENT"],
  "guild_ids": ["123"],
  "channel_ids": ["456"],
  "message_types": ["MESSAGE_CREATE"]
}
```

```json
{
  "type": "QQBotListenerNode",
  "name": "qq_listener",
  "app_id": "[[qq_bot_a#secret.app_id]]",
  "app_secret": "[[qq_bot_a#secret.app_secret]]",
  "account_id": "default",
  "scenes": ["c2c", "group", "channel"],
  "message_types": ["MESSAGE_CREATE"]
}
```

## Security Considerations

- Store bot tokens and app secrets only in the credential vault or equivalent secret references.
- Never log raw authorization headers or full Gateway identify payloads.
- Validate that a workflow is allowed to bind to the referenced credentials.
- Enforce Discord intent declarations explicitly so the runtime does not request more data than needed.
- Isolate QQ access token caches and session state per AppID to avoid cross-bot contamination.
- Keep raw-payload retention configurable because message content may contain sensitive user data.
- Add per-subscription backoff and circuit-breaker behavior to avoid token lockout or abuse from bad credentials.
- Surface Tencent whitelist and permission failures distinctly because they usually require operator changes outside Orcheo rather than transport retries.

## Performance Considerations

- Telegram long polling should use blocking timeouts to avoid busy loops.
- Discord Gateway sessions should be shared per bot identity in future phases to avoid unnecessary duplicate sessions.
- QQ Gateway sessions should be shared per QQ bot identity in future phases to avoid unnecessary duplicate sessions.
- Listener dispatch must remain lightweight and push real work to the existing async worker queue.
- Dedupe storage should use TTL-based cleanup to avoid unbounded growth.
- The supervisor should cap concurrent reconnect storms through jittered backoff.
- QQ session startup must respect the provider-reported `session_start_limit`, and send-path retries must honor QQ-specific write-rate errors instead of retrying blindly.

## Error Handling

- **QQ token expiration and refresh**
  - Cache tokens per AppID.
  - Refresh proactively before expiry.
  - Use a single-flight refresh lock per AppID so multiple adapters do not stampede the token endpoint.
  - During the documented 60-second overlap window, allow in-flight requests to finish with the old token while new requests switch to the new token.
  - Treat invalid-token responses after a refresh as credential failures, not as generic network errors.

- **Private-deployment connectivity**
  - Distinguish DNS failures, TLS handshake failures, corporate proxy interference, outbound firewall blocks, idle TCP timeout resets, and WSS upgrade failures.
  - Mark subscriptions `degraded` when reachability is broken for a sustained period and surface the failing Tencent hostname in health output.
  - Require preflight checks for `bots.qq.com`, `api.sgroup.qq.com`, `sandbox.api.sgroup.qq.com`, and the runtime Gateway URL returned by Tencent.

- **Platform-specific rate limiting**
  - Telegram: honor HTTP 429 and `Retry-After` where returned.
  - Discord: honor Gateway identify/session-start limits and reconnect backoff rules.
  - QQ: honor `session_start_limit`, HTTP 429, and known OpenAPI error families such as `ChannelHitWriteRateLimit` and channel-level write throttling.
  - All providers: keep retry state per subscription so one noisy bot does not starve others.

## Testing Strategy

- **Unit tests**: node config validation, subscription compilation, Telegram offset handling, Discord heartbeat/reconnect state machine, QQ token/session state machine, dedupe logic.
- **Integration tests**: mocked Telegram `getUpdates` loop, mocked Discord Gateway session, mocked QQ `/gateway` plus WebSocket session, end-to-end dispatch into workflow runs.
- **Persistence tests**: restart recovery for Telegram offsets, Discord resume data, and QQ session resumption.
- **Template validation**: validate one Studio template per platform with `listener -> AgentNode -> supported reply node` wiring and one Studio template with all three listeners feeding a shared `AgentNode`, then record the resulting `template_version`, `min_orcheo_version`, and `validated_provider_api`.
- **Manual QA checklist**: private-network deployment, token rotation, workflow pause/resume, two independent Telegram bots, one Discord bot in DM and guild scenarios, two independent QQ bots using different AppIDs, QQ whitelist/permission failures, and blocked-egress recovery drills.

## Rollout Plan

1. Phase 1: Implement subscription persistence and Telegram listener runtime behind a feature flag.
2. Phase 2: Add Discord and QQ Gateway runtimes and validate reconnect behavior plus token/session persistence.
3. Phase 3: Expose listener health in operational surfaces and ship Studio templates for each single-listener workflow plus the shared three-listener workflow.
4. Phase 4: Optimize shared-session fan-out and broader operational controls.

## Open Issues

- The exact persistence home for listener ownership and health may belong in the backend app package rather than `src/orcheo/` core; implementation should follow the same backend-versus-worker boundary that already exists in the repository.
- The shared three-listener template should remove ambiguity about how multiple listener nodes connect to one downstream `AgentNode`; if Studio or runtime semantics need adjustment, that template is the acceptance vehicle for the decision.
- The same template set should remove ambiguity about reply transport ownership. If Discord or QQ lacks a send node, that is not deferred work; it is a required dependency to finish the templates.

---

## Revision History

| Date | Author | Changes |
|---|---|---|
| 2026-03-11 | Codex | Initial draft |
