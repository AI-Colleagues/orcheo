# Discord Listener Operations

- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2026-03-11

## Scope

This note covers the shipped private-listener workflow artifact for Discord:

- Studio template: `template-discord-private-listener`
- Graph shape: `DiscordBotListenerNode -> AgentNode -> MessageDiscordNode`
- Required credentials: `[[discord_bot_token]]`, `[[openai_api_key]]`

## Deployment Flow

1. Import the `Discord Private Listener` template in Studio.
2. Provide `[[discord_bot_token]]` and `[[openai_api_key]]`.
3. Confirm the Discord bot has the intents required by the workflow, especially `MESSAGE_CONTENT` if reply generation depends on full user text.
4. Run the listener supervisor alongside the normal Orcheo worker so the Discord Gateway subscription stays leased and healthy.
5. Verify outbound-only reachability to Discord HTTPS and Gateway endpoints before enabling the workflow on a private host.

## Reply Node Audit

- Discord: supported by `MessageDiscordNode`; shipped and used by the template.
- Telegram: supported by `MessageTelegramNode`.
- QQ: supported by `MessageQQNode`.

## Template Acceptance And Versioning

- Acceptance requires clean Studio import, no manual JSON edits, and a runnable reply path using supported nodes only.
- `template_version`, `min_orcheo_version`, `validated_provider_api`, reply-node contracts, owner, and revalidation triggers are carried in the Studio template metadata and stored with the ingested workflow version metadata.
- Major version bumps are required when the workflow shape or provider contract changes.
- Minor version bumps are sufficient for validation refreshes against the same workflow and provider contract.

## Revalidation Triggers

- Discord Gateway major-version change.
- `MessageDiscordNode` reply contract change.
- Listener dispatch payload or supervisor lease contract change.
