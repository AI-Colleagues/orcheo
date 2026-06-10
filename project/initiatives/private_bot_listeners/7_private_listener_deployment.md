# Private Listener Deployment

- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2026-03-11

## Scope

This note covers production-oriented deployment guidance for the private listener initiative:

- Templates: `template-telegram-private-listener`, `template-discord-private-listener`, `template-qq-private-listener`, `template-private-bot-shared-listener`
- Runtime responsibilities: listener supervision, workflow workers, secret management, and operator health checks

## Topology

1. Run the backend API and repository as usual.
2. Run the workflow worker pool separately from the listener supervisor runtime so long-lived polling and Gateway loops do not compete with ordinary workflow execution.
3. Point both the listener supervisor and worker pool at the same workflow repository so listener subscriptions, cursors, dedupe windows, and dispatched runs stay consistent.
4. Publish only outbound access from the listener supervisor to provider HTTPS and WSS endpoints; no public callback URL is required for Telegram, Discord, or QQ listener operation.

## Secret Management

- Store `[[telegram_token]]`, `[[discord_bot_token]]`, `[[qq_app_id]]`, `[[qq_client_secret]]`, and `[[openai_api_key]]` in the Orcheo vault rather than inline workflow edits.
- Keep QQ AppID and client secret together because the listener runtime and `MessageQQNode` both rely on the same token cache contract.
- Do not enable sensitive logging for listener runtimes in production because provider payloads can include user message text.

## Health And Controls

- Use the workflow listener endpoints to inspect live listener state: `GET /api/workflows/{workflow_ref}/listeners`.
- Use the metrics endpoint for operational summaries and alerts: `GET /api/workflows/{workflow_ref}/listeners/metrics`.
- Pause and resume individual subscriptions through `POST /api/workflows/{workflow_ref}/listeners/{subscription_id}/pause` and `/resume`.
- Treat repeated QQ whitelist failures, Discord reconnect loops, and Telegram polling backoff as operator-actionable alerts rather than waiting for passive recovery forever.

## Template Usage

- Single-platform templates validate the direct path `listener -> AgentNode -> provider send node`.
- The shared template validates that Telegram, Discord, and QQ listeners can feed one `AgentNode` while the reply is routed back through `MessageTelegramNode`, `MessageDiscordNode`, or `MessageQQNode` according to `inputs.platform`.
- Revalidate and bump template metadata when provider APIs or reply-node contracts change; Studio now blocks stale private-listener templates from being instantiated until that metadata is updated.
