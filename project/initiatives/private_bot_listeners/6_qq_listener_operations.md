# QQ Listener Operations

- **Author:** Codex
- **Owner:** Shaojie Jiang
- **Date:** 2026-03-11

## Scope

This note covers the shipped private-listener workflow artifact for QQ:

- Studio template: `template-qq-private-listener`
- Graph shape: `QQBotListenerNode -> AgentNode -> MessageQQNode`
- Required credentials: `[[qq_app_id]]`, `[[qq_client_secret]]`, `[[openai_api_key]]`

## Deployment Flow

1. Import the `QQ Private Listener` template in Studio.
2. Provide `[[qq_app_id]]`, `[[qq_client_secret]]`, and `[[openai_api_key]]`.
3. Keep the bot on Tencent's required whitelist and confirm outbound reachability to `bots.qq.com`, `api.sgroup.qq.com`, and the Gateway URL returned by `/gateway/bot`.
4. Run the listener supervisor alongside the normal Orcheo worker so the QQ Gateway subscription stays leased and healthy.
5. If the bot is still sandbox-only, keep the template in `sandbox=False` only after Tencent production access is confirmed.

## Reply Node Audit

- QQ: supported by `MessageQQNode`; shipped and used by the template.
- Telegram: supported by `MessageTelegramNode`.
- Discord: supported by `MessageDiscordNode`.

## Template Acceptance And Versioning

- Acceptance requires clean Studio import, no manual JSON edits, and a runnable reply path using supported nodes only.
- `template_version`, `min_orcheo_version`, `validated_provider_api`, reply-node contracts, owner, and revalidation triggers are carried in the Studio template metadata and stored with the ingested workflow version metadata.
- Major version bumps are required when the workflow shape or provider contract changes.
- Minor version bumps are sufficient for validation refreshes against the same workflow and provider contract.

## Revalidation Triggers

- QQ Bot API v2 major-version change.
- `MessageQQNode` reply contract change.
- Listener dispatch payload or supervisor lease contract change.
