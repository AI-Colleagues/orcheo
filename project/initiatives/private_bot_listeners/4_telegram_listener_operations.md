# Telegram Listener Operations

- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2026-03-11

## Scope

This note covers the first shipped private-listener workflow artifact for the initiative:

- Studio template: `template-telegram-private-listener`
- Graph shape: `TelegramBotListenerNode -> AgentNode -> MessageTelegramNode`
- Required credentials: `[[telegram_token]]`, `[[openai_api_key]]`

## Deployment Flow

1. Import the `Telegram Private Listener` template in Studio.
2. Provide `[[telegram_token]]` and `[[openai_api_key]]`.
3. Adjust `configurable.ai_model` and `configurable.system_prompt` if the defaults are not suitable.
4. Run the listener supervisor alongside the normal Orcheo worker so the Telegram polling subscription stays leased and healthy.
5. Verify outbound-only reachability to Telegram Bot API endpoints before enabling the workflow on a private host.

## Reply Node Audit

- Telegram: supported by `MessageTelegramNode`; shipped and used by the template.
- Discord: supported by `MessageDiscordNode`.
- QQ: supported by `MessageQQNode`.

## Template Acceptance And Versioning

- Acceptance requires clean Studio import, no manual JSON edits, and a runnable reply path using supported nodes only.
- `template_version`, `min_orcheo_version`, `validated_provider_api`, reply-node contracts, owner, and revalidation triggers are carried in the Studio template metadata and stored with the ingested workflow version metadata.
- Major version bumps are required when the workflow shape or provider contract changes.
- Minor version bumps are sufficient for validation refreshes against the same workflow and provider contract.

## Revalidation Triggers

- Telegram Bot API major-version change.
- `MessageTelegramNode` reply contract change.
- Listener dispatch payload or supervisor lease contract change.
