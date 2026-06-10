# Project Plan

## For Private Bot Listener Nodes

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-03-11
- **Status:** Approved

---

## Overview

Deliver optional workflow-attached listener nodes that keep Telegram, Discord, and QQ bots connected without requiring a public Orcheo callback URL. For QQ, the implementation should use Tencent's AppID/AppSecret authentication and `/gateway` WebSocket session model, matching the official OpenClaw plugin approach.
The deliverable also includes Studio templates that prove the listener nodes work in actual workflows: one template per platform with an `AgentNode` generating the reply, plus one template with Telegram, Discord, and QQ listeners in parallel sharing the same `AgentNode`. If a platform is missing a reply/send node, adding that minimal outbound node stays inside scope until the template is runnable end to end.

**Related Documents:**

- Requirements: [1_requirements.md](1_requirements.md)
- Design: [2_design.md](2_design.md)

---

## Milestones

### Milestone 1: Listener Foundations

**Description:** Add the core subscription model and runtime supervision needed for any long-lived listener.

#### Task Checklist

- [x] Task 1.1: Define listener node schemas for Telegram and Discord.
  - Dependencies: None
- [x] Task 1.2: Extend workflow activation logic to extract listener subscriptions from workflow graphs.
  - Dependencies: Task 1.1
- [x] Task 1.3: Add persistence for `listener_subscription`, `listener_cursor`, and short-lived dedupe records.
  - Dependencies: Task 1.2
- [x] Task 1.4: Implement a listener supervisor process with leasing, health reporting, and graceful shutdown.
  - Dependencies: Task 1.3
- [x] Task 1.5: Add `triggered_by="listener"` dispatch plumbing into the existing run queue.
  - Dependencies: Task 1.4
- [x] Task 1.6: Define the workflow/runtime contract that allows multiple listener nodes to feed the same downstream `AgentNode` and preserve correct reply routing metadata.
  - Dependencies: Task 1.5
- [x] Task 1.7: Audit reply-node availability for Telegram, Discord, and QQ, and mark missing outbound nodes as required deliverables for template completion instead of follow-up work.
  - Dependencies: Task 1.6
- [x] Task 1.8: Define Studio template acceptance criteria, owner metadata, and template versioning rules tied to Orcheo and provider API versions.
  - Dependencies: Task 1.6

---

### Milestone 2: Telegram Private Listener

**Description:** Deliver Telegram long polling as the first fully supported private-network bot listener that does not require a public callback URL.

#### Task Checklist

- [x] Task 2.1: Implement `TelegramBotListenerNode` validation and Studio/SDK registration.
  - Dependencies: Milestone 1
- [x] Task 2.2: Implement Telegram `getUpdates` adapter with offset persistence and backoff.
  - Dependencies: Task 2.1
- [x] Task 2.3: Normalize Telegram updates into listener dispatch payloads compatible with downstream workflows.
  - Dependencies: Task 2.2
- [x] Task 2.4: Reuse or align with existing `TelegramEventsParserNode` and `MessageTelegramNode` behavior where sensible.
  - Dependencies: Task 2.3
- [x] Task 2.5: Add tests for polling, restart recovery, dedupe, and two independent Telegram bot configurations.
  - Dependencies: Task 2.4
- [x] Task 2.6: Add a Studio template private Telegram bot workflow with `TelegramBotListenerNode -> AgentNode -> MessageTelegramNode`.
  - Dependencies: Task 2.5
- [x] Task 2.7: Add operator documentation for the Telegram listener template and deployment flow.
  - Dependencies: Task 2.5

---

### Milestone 3: Discord Gateway Listener

**Description:** Add Discord message listening through the Gateway for private-network deployments.

#### Task Checklist

- [x] Task 3.1: Implement `DiscordBotListenerNode` validation, intent configuration, and workflow compilation.
  - Dependencies: Milestone 1
- [x] Task 3.2: Implement the Gateway adapter with `GET /gateway/bot`, heartbeat, identify, reconnect, and resume handling.
  - Dependencies: Task 3.1
- [x] Task 3.3: Add event filtering for guilds, channels, DMs, and message types.
  - Dependencies: Task 3.2
- [x] Task 3.4: Add dispatch normalization and dedupe for Discord message events.
  - Dependencies: Task 3.3
- [x] Task 3.5: Add tests for heartbeat timing, resume flow, identify/session-start rate limits, message content intent handling, and event dispatch.
  - Dependencies: Task 3.4
- [x] Task 3.6: Deliver a supported Discord outbound reply node if an adequate one does not already exist for listener-triggered workflows.
  - Dependencies: Task 3.5, Task 1.7
- [x] Task 3.7: Add a Studio template private Discord bot workflow with `DiscordBotListenerNode -> AgentNode -> MessageDiscordNode` or the final equivalent node name.
  - Dependencies: Task 3.6
- [x] Task 3.8: Add operator documentation for the Discord listener template and deployment flow.
  - Dependencies: Task 3.7

---

### Milestone 4: Operations and Productization

**Description:** Make listener-based bots operable in production private deployments.

#### Task Checklist

- [x] Task 4.1: Add listener health surfaces to API/UI/CLI as appropriate.
  - Dependencies: Milestone 2, Milestone 3
- [x] Task 4.2: Add pause/resume or disable controls for listener subscriptions.
  - Dependencies: Task 4.1
- [x] Task 4.3: Add metrics and alerts for stalled listeners, reconnect loops, and dispatch failures.
  - Dependencies: Task 4.1
- [x] Task 4.4: Add a Studio template with Telegram, Discord, and QQ listeners in parallel sharing one `AgentNode`, and validate that each listener replies with the matching bot identity through supported workflow nodes only.
  - Dependencies: Milestone 2, Milestone 3, Milestone 5
- [x] Task 4.5: Document deployment topology for private hosts, worker separation, secret management, and template workflow usage.
  - Dependencies: Task 4.3
- [x] Task 4.6: Add template compatibility tracking so provider API changes or reply-node contract changes trigger template revalidation and a version bump.
  - Dependencies: Task 4.4, Task 1.8

---

### Milestone 5: QQ Gateway Listener

**Description:** Add QQ message listening using Tencent's official AppID/AppSecret and Gateway WebSocket model.

#### Task Checklist

- [x] Task 5.1: Implement `QQBotListenerNode` validation and workflow compilation.
  - Dependencies: Milestone 1
- [x] Task 5.2: Implement QQ access-token retrieval and per-AppID token caching.
  - Dependencies: Task 5.1
- [x] Task 5.3: Implement QQ `/gateway` or `/gateway/bot` discovery, WebSocket connection, heartbeat, reconnect, resume handling, and provider-reported session-start limits.
  - Dependencies: Task 5.2
- [x] Task 5.4: Normalize QQ C2C, group, and channel message events into listener dispatch payloads.
  - Dependencies: Task 5.3
- [x] Task 5.5: Add tests for multi-account isolation, token refresh overlap windows, session resume, rate limiting, whitelist failures, and event dispatch.
  - Dependencies: Task 5.4
- [x] Task 5.6: Deliver a supported QQ outbound reply node if an adequate one does not already exist for listener-triggered workflows.
  - Dependencies: Task 5.5, Task 1.7
- [x] Task 5.7: Add a Studio template private QQ bot workflow with `QQBotListenerNode -> AgentNode -> MessageQQNode` or the final equivalent node name.
  - Dependencies: Task 5.6, Task 1.8
- [x] Task 5.8: Add operator documentation for the QQ listener template and deployment flow.
  - Dependencies: Task 5.7

---

## Revision History

| Date | Author | Changes |
|---|---|---|
| 2026-03-11 | Codex | Initial draft |
