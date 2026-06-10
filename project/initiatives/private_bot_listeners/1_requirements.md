# Requirements Document: Private Bot Listener Nodes

## METADATA

- **Authors:** Codex
- **Project/Feature Name:** Private Bot Listener Nodes
- **Type:** Enhancement
- **Summary:** Add optional workflow-attached listener nodes that receive Telegram, Discord, and QQ bot messages without requiring a public Orcheo backend URL.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-11

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|---|---|---|---|
| Telegram Bot API | https://core.telegram.org/bots/api | Telegram | Bot API Reference |
| Discord Gateway | https://docs.discord.com/developers/events/gateway | Discord | Gateway Documentation |
| QQ Bot Docs | https://bot.q.qq.com/wiki | Tencent QQ | QQ Bot Official Docs |
| QQ Bot API v2 Overview | https://bot.q.qq.com/wiki/develop/api-v2/ | Tencent QQ | API v2 Root |
| QQ Bot API v2 Auth | https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html | Tencent QQ | AccessToken and OpenAPI Authentication |
| QQ Bot API v2 Gateway | https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html | Tencent QQ | Gateway, Heartbeat, and Resume |
| QQ Bot API v2 Error Codes | https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/error-trace/openapi.html | Tencent QQ | OpenAPI Error Catalogue |
| QQ OpenClaw Plugin | https://github.com/tencent-connect/openclaw-qqbot | Tencent QQ | Official OpenClaw QQ Bot Plugin |
| QQ Bot SDK | https://github.com/tencent-connect/botgo | Tencent QQ | BotGo SDK README |
| Requirements | [1_requirements.md](1_requirements.md) | Shaojie Jiang | Private Bot Listener Requirements |
| Design | [2_design.md](2_design.md) | Shaojie Jiang | Private Bot Listener Design |
| Plan | [3_plan.md](3_plan.md) | Shaojie Jiang | Private Bot Listener Plan |

Validation notes checked on 2026-03-11:

- Tencent's official QQ docs still publish the listener-related material under API v2 paths, including AccessToken auth and Gateway session flows.
- The API v2 auth docs specify `https://bots.qq.com/app/getAppAccessToken`, a default `access_token` lifetime of 7200 seconds, and a 60-second overlap window near expiry where a newly fetched token coexists with the old one.
- The API v2 Gateway docs describe heartbeat, resume, invalid-session handling, and `GET /gateway/bot` responses that include `session_start_limit`.
- `tencent-connect/openclaw-qqbot` and `tencent-connect/botgo` were both reachable on GitHub, not archived, and not disabled when checked on 2026-03-11.

## PROBLEM DEFINITION

### Objectives

Enable Orcheo workflows to continuously receive bot messages from Telegram, Discord, and QQ without exposing the Orcheo backend through a public inbound webhook URL. Keep these listeners optional and workflow-scoped so multiple independently configured bots can coexist.
Ship Studio template workflows that exercise both single-listener and multi-listener reply flows so the feature's workflow semantics are validated by end-to-end usage, not only transport-layer implementation.

### Target users

Operators and workflow authors who want Orcheo-hosted chatbots to run from private networks, laptops, or VPC-only deployments.

### User Stories

| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---|---|---|---|---|
| Workflow author | attach a Telegram listener to one workflow and a different Telegram listener to another | each bot can react independently | P0 | Two workflows configured with different Telegram bot credentials each receive only their own updates |
| Workflow author | attach a Discord listener node to a workflow | I can react to guild or DM messages without inbound webhooks | P0 | Discord message events create workflow runs from a maintained Gateway session |
| Workflow author | attach a QQ listener node to a workflow | I can react to QQ private, group, or channel messages without inbound webhooks | P0 | QQ message events create workflow runs from a maintained QQ gateway session using the configured AppID and AppSecret |
| Workflow author | configure two QQ bots on different workflows | each QQ bot can receive and reply with its own identity | P0 | Two workflows configured with different QQ AppID/AppSecret pairs each receive only their own QQ events and send replies with the matching bot account |
| Workflow author | start from a Studio template for a Telegram, Discord, or QQ listener bot | I can quickly deploy a working listener workflow with AI-generated replies | P0 | Studio provides one template per listener platform with the listener feeding an `AgentNode` that generates the reply message, and the template is runnable end to end without manual API-call patching |
| Workflow author | start from a Studio template with Telegram, Discord, and QQ listeners sharing one bot brain | I can validate that different listeners can trigger the same downstream workflow logic | P0 | Studio provides a template where three listeners run in parallel, the same `AgentNode` decides the reply, and each event still replies through the matching bot identity using actual workflow nodes rather than undocumented placeholders |
| Operator | run Orcheo behind NAT or on a private host | I do not need to expose a public callback URL | P0 | Telegram, Discord, and QQ listeners function with outbound-only connectivity |
| Operator | restart Orcheo services safely | listeners recover without duplicating or losing large batches of events | P0 | Listener cursors and sessions resume with bounded duplication after restart |
| Workflow author | choose whether a workflow uses listener-based bots or existing webhook-based integrations | the feature remains optional | P1 | Workflows without listener nodes have no extra runtime overhead |

### Context, Problems, Opportunities

Orcheo already supports webhook triggers, HTTP polling trigger configuration, Telegram send/parse nodes, Discord outbound webhooks, and an asynchronous worker model. That is enough to model bot reactions once an event enters the system, but not enough to keep a bot connected when the platform expects long polling or a persistent gateway connection. The missing capability is a runtime-managed listener plane that owns outbound polling or WebSocket sessions and turns external bot events into workflow runs.

Telegram is the simplest starting point because the Bot API supports `getUpdates` long polling when no webhook is configured. Discord also supports private-network operation through the Gateway WebSocket, provided Orcheo maintains heartbeats, reconnect/resume logic, and the required intents.
QQ needs the same kind of outbound listener runtime rather than an inbound webhook. Tencent's official API v2 docs and the `@tencent-connect/openclaw-qqbot` plugin show that this is feasible: they use `AppID` and `clientSecret` (the field often referred to in product language as `AppSecret`) to fetch an access token from `https://bots.qq.com/app/getAppAccessToken`, call the QQ Bot `/gateway` or `/gateway/bot` API, open a WebSocket session, and persist session resume state. That evidence supports treating QQ as another gateway-based listener integration alongside Discord.

### Product Goals and Non-Goals

Goals:

- Add workflow-level listener nodes for Telegram and Discord that work without public inbound URLs.
- Add workflow-level listener nodes for QQ that work with Tencent's AppID/AppSecret and gateway session model.
- Define a listener runtime that can manage multiple independently configured bots.
- Preserve Orcheo's existing execution model: listeners dispatch normal workflow runs with structured inputs.
- Support multiple bot identities per platform when each uses separate credentials or account IDs.
- Ship Studio templates that demonstrate each listener platform paired with an `AgentNode` that generates reply text.
- Ship a Studio template that demonstrates Telegram, Discord, and QQ listeners in parallel feeding one shared `AgentNode` and preserving correct per-platform reply routing.
- Treat missing outbound reply nodes as in-scope gaps for this initiative: if Discord or QQ lacks a reusable send node, the initiative must add the minimal node or equivalent first-class reply transport needed for the templates to work end to end.

Non-goals:

- Replacing existing webhook triggers for platforms that already work well with webhooks.
- Supporting multiple workflows independently long-polling the same Telegram bot token in v1.
- Building a general-purpose IM gateway abstraction for every chat platform.
- Guaranteeing exactly-once delivery across reconnects or provider retries.

## PRODUCT DEFINITION

### Requirements

**P0 (must have)**

- Introduce listener-capable workflow nodes for Telegram and Discord.
- A workflow activation step registers listener subscriptions derived from those nodes.
- A runtime listener supervisor keeps outbound polling or WebSocket sessions alive and dispatches workflow runs on incoming events.
- Listener state persists platform cursors or session-resume data needed for restart recovery.
- Telegram listener support uses Bot API long polling (`getUpdates`) and documents that it cannot run concurrently with a Telegram webhook on the same bot token.
- Discord listener support uses Gateway sessions, heartbeat handling, reconnect/resume, and configurable intents/event filters.
- QQ listener support uses Tencent AppID/AppSecret credentials, access-token retrieval, `/gateway` discovery, WebSocket heartbeat/reconnect, and session-resume persistence.
- Each listener instance is optional and scoped to the workflow version/configuration that declared it.
- Multiple bots per platform are supported when they use different credentials or app identities.
- Incoming events are normalized into a stable Orcheo payload shape with platform metadata, sender identity, chat/channel identity, raw event payload, and dedupe key.
- Delivery is at-least-once with provider-aware deduplication to reduce duplicates across reconnects.
- Observability covers listener health, reconnects, last event time, dispatch failures, and cursor/session lag.
- Studio template workflows are provided for Telegram, Discord, and QQ individually, each wiring the listener into an `AgentNode` that generates the reply message and then into an actual outbound reply node supported by Orcheo.
- A Studio template workflow is provided with Telegram, Discord, and QQ listeners in parallel, using a shared `AgentNode` for reply generation so implementation must validate shared-downstream workflow behavior before the initiative is complete.
- If an outbound reply node does not already exist for a required template platform, delivering that minimal outbound node is part of the feature scope; templates cannot be considered complete if they depend on manual HTTP nodes, undocumented custom code, or TODO placeholders for reply delivery.
- Studio templates include explicit acceptance criteria, version metadata, and ownership so provider/API evolution does not silently invalidate them.

**P1 (nice to have)**

- Shared-session fan-out for multiple workflows that intentionally use the same Discord bot token.
- Shared-session fan-out for multiple workflows that intentionally use the same Telegram bot token plus routing rules.
- Built-in filter fields for message type, guild/server, channel/chat, and bot-mention requirements.
- Pause/resume controls for listener subscriptions from the backend API or UI.
- Example workflows and docs for private-network bot deployment.
- Support multiple named QQ bot accounts per workspace or workflow deployment model.

### Designs (if applicable)

See [2_design.md](2_design.md) for the listener supervisor, platform adapters, and workflow integration model.

### [Optional] Other Teams Impacted

- **Backend/runtime:** New long-lived listener supervision and state persistence.
- **Studio/SDK:** New node definitions and activation-time validation rules.
- **Operations:** New health metrics, credentials, and restart procedures for listener processes.

## TECHNICAL CONSIDERATIONS

### Architecture Overview

Listener nodes are compiled into subscription records when a workflow version is activated. A new listener supervisor process, likely co-located with the existing worker deployment, owns the long-lived outbound connections: Telegram long-polling loops plus Discord and QQ Gateway sessions. When a platform event arrives, the supervisor normalizes it, stores updated cursor/session state, and enqueues a normal workflow run with `triggered_by=listener`.

### Technical Requirements

- New node classes and registry metadata for private bot listeners.
- Subscription persistence keyed by workspace, workflow version, node name, platform, and credential identity.
- A listener runtime process with health reporting, graceful shutdown, and restart recovery.
- Telegram adapter with `getUpdates`, offset persistence, `allowed_updates`, timeout control, and backoff.
- Discord adapter with `GET /gateway/bot`, IDENTIFY/RESUME support, heartbeat scheduling, intents validation, and reconnect strategy.
- QQ adapter with API v2 AppID/clientSecret token exchange, `GET /gateway` or `GET /gateway/bot`, heartbeat, reconnect/resume, `session_start_limit` awareness, and per-account token/session isolation.
- Token lifecycle handling for QQ that refreshes before expiry, uses the documented 60-second overlap window safely, and prevents duplicate concurrent refreshes for the same AppID.
- Credential resolution through the existing vault/reference mechanism; secrets must never be logged.
- Dispatch path integrated with the existing run repository and worker queue.
- Idempotency storage keyed by provider event identifiers (`update_id`, Discord snowflakes/event tuples, QQ event IDs if supported).
- Workflow validation that rejects conflicting listener configurations where the provider forbids them.
- Private-network deployments must validate outbound reachability to provider HTTPS and WSS endpoints, including `bots.qq.com`, `api.sgroup.qq.com`, `sandbox.api.sgroup.qq.com`, and the Gateway URL returned by Tencent.
- Error handling must distinguish credential failures, token-expiry refresh paths, invalid-session resume failures, transient network faults, and provider-specific rate limiting or whitelist restrictions.

### AI/ML Considerations (if applicable)

Not applicable to the transport layer. Downstream workflows may still use Orcheo AI nodes after the listener dispatch.

## MARKET DEFINITION (for products or large features)

Not applicable; this is an internal platform capability.

## LAUNCH/ROLLOUT PLAN

### Success metrics

| KPIs | Target & Rationale |
|---|---|
| [Primary] Telegram listener dispatch success | 95%+ over 500 staged messages |
| [Primary] Discord listener dispatch success | 95%+ over 500 staged messages |
| [Primary] QQ listener dispatch success | 95%+ over 500 staged messages |
| [Secondary] Listener recovery time after restart | < 60 seconds to resume healthy state |
| [Guardrail] Duplicate-dispatch rate | < 1% in reconnect and restart tests |
| [Delivery] Studio template readiness | Individual Telegram/Discord/QQ listener templates and one shared three-listener template are shipped, versioned, and validated against the acceptance checklist |

### Rollout Strategy

Ship Telegram first because Orcheo already has Telegram send/parser primitives and Telegram officially supports long polling. Add Discord and QQ next on the shared gateway-listener runtime foundation. For QQ, build directly on Tencent's documented AppID/AppSecret plus gateway session model instead of assuming a webhook relay is required.

### Experiment Plan (if applicable)

Not applicable. This is an infrastructure/platform feature.

### Estimated Launch Phases (if applicable)

| Phase | Target | Description |
|---|---|---|
| **Phase 1** | Internal Telegram staging | Validate long polling, cursor persistence, and workflow dispatch |
| **Phase 2** | Internal Discord staging | Validate Gateway session lifecycle, intents, and dispatch reliability |
| **Phase 3** | Internal QQ staging | Validate AppID/AppSecret token exchange, gateway session lifecycle, and multi-account isolation |
| **Phase 4** | Beta users on private deployments | Enable selected private-network bot workflows and collect operational feedback |

## HYPOTHESIS & RISKS

- **Hypothesis:** If Orcheo owns long-lived outbound listener sessions as first-class runtime resources, private-network deployments can run chatbots without public inbound webhooks.
- **Confidence:** High for Telegram, medium for Discord, and medium to high for QQ because Tencent's maintained OpenClaw QQ plugin demonstrates outbound gateway sessions with access-token exchange and session resume as of 2026-03-11.
- **Risk:** Telegram and Discord can produce duplicate events around reconnects or restarts.
  - **Mitigation:** Persist cursors, store recent dedupe keys, and accept at-least-once semantics.
- **Risk:** QQ gateway handling adds another provider-specific state machine with token refresh and resume behavior.
  - **Mitigation:** Reuse the same supervisor contracts used for Discord and model token/session persistence per QQ account.
- **Risk:** QQ private-network deployments may fail because required Tencent HTTPS/WSS endpoints are unreachable or because the bot is blocked by provider-side whitelist or channel restrictions.
  - **Mitigation:** Add deployment checks for Tencent domains, surface whitelist-related errors explicitly, and treat failed reachability checks as preflight blockers rather than retrying indefinitely.
- **Risk:** Platform-specific rate limits can cause reconnect storms, dropped sends, or repeated outbound failures.
  - **Mitigation:** Persist provider limit state, honor `Retry-After` or session-start guidance where available, and use per-platform backoff rules rather than a single generic retry loop.
- **Risk:** Long-lived listeners increase operational complexity versus stateless webhook handling.
  - **Mitigation:** Isolate listener supervision into a dedicated process with explicit health metrics and bounded restart logic.

## APPENDIX

### Assumptions

- The target deployment can maintain outbound HTTPS and WSS connections.
- For QQ specifically, outbound access to `bots.qq.com`, `api.sgroup.qq.com`, `sandbox.api.sgroup.qq.com`, and Tencent-provided Gateway WSS URLs is a hard prerequisite.
- Multiple workflows using different bot credentials on the same platform are the primary multi-bot use case.
- Native support for multiple workflows sharing the exact same bot identity is a later optimization, not a P0 requirement.
- Delivering the Studio templates is part of the feature definition because those templates provide the concrete validation path for both single-listener reply behavior and shared downstream logic across Telegram, Discord, and QQ.
- A "complete" template means the reply path is expressed with supported workflow nodes. Missing Discord or QQ send nodes are therefore product gaps to close inside this initiative, not exceptions that allow incomplete templates.
- Some QQ capabilities remain permission-gated by Tencent configuration, such as whitelist and channel-scoped constraints, so Orcheo should surface those failures as operator action items instead of masking them as generic transport errors.

### Template acceptance and versioning

- Every delivered template must import into the current Studio schema without manual JSON edits.
- Every delivered template must run end to end with supported nodes only: `listener -> AgentNode -> provider reply node`.
- Every delivered template must include operator notes listing required credentials, minimum Orcheo version, and the target provider API major version.
- Template metadata must include a semantic `template_version`, `min_orcheo_version`, and `validated_provider_api` value such as `telegram-bot-api`, `discord-gateway-v10`, or `qq-bot-api-v2`.
- Breaking workflow-shape changes or provider API changes require a template major-version bump; validation-only refreshes use a minor-version bump.
- Each template must name an owner and a revalidation trigger: provider API major change, reply-node behavior change, or listener runtime contract change.
