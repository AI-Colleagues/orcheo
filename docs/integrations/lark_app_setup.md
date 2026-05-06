# Lark App Setup For Orcheo

This guide captures the Lark app settings that matter for Orcheo listener workflows.

It is based on Orcheo debugging experience plus the Feishu/OpenClaw setup guide:
- [OpenClaw Feishu guide](https://docs.openclaw.ai/channels/feishu)
- [Lark Open Platform](https://open.larksuite.com/app)

## When this guide applies

Use this when you want an Orcheo workflow to receive Lark messages through the Lark long-connection listener.

For the shared WeCom/Lark template:
- Lark traffic requires valid Lark credentials.
- WeCom traffic requires valid WeCom credentials.
- Missing WeCom credentials should not block Lark runs in the fixed template, but the WeCom listener will still stay blocked until configured.

## Required Lark app configuration

### 1. Create the correct app

Create an enterprise app in the Lark Open Platform.

For international deployments, use the Lark global domain:
- `https://open.larksuite.com`

The App ID should look like `cli_xxx`.

### 2. Copy and store credentials

From the app basic information page, copy:
- App ID
- App Secret

In Orcheo, store them as the credentials used by the Lark listener and Lark send-message nodes.

### 3. Enable bot capability

In the app capabilities:
- Enable the bot capability
- Set a bot name

If bot capability is not enabled, direct chat behavior will be inconsistent even if the app credentials are valid.

### 4. Grant the minimum useful permissions

The exact permission set depends on your workflow, but for basic Orcheo receive/reply usage the app should have the message read/send permissions needed for bot messaging.

At minimum, verify the app has permissions equivalent to:
- `im:message:send_as_bot`
- `im:message:readonly`
- `im:message.p2p_msg:readonly`
- `im:message.group_at_msg:readonly`
- `im:resource`

If you plan to read group metadata or members, add the corresponding chat permissions as well.

### 5. Use long connection event subscription

In Event Subscription:
- Choose long connection / WebSocket mode
- Add the event `im.message.receive_v1`

This event is required for Orcheo's Lark listener.

Important:
- Adding the event is not enough by itself
- The app version must be published after the event is added

## Publish requirements

After changing permissions, bot capability, or event subscriptions:
1. Create or update the app version
2. Publish the version
3. Confirm the published version contains the message receive event

This matters because Orcheo may connect to Lark successfully while still receiving no messages if the published app version does not include `im.message.receive_v1`.

One detail from debugging:
- The Lark UI may show a localized event label such as `接收消息`
- The underlying event type still needs to be `im.message.receive_v1`

## Orcheo-side checklist

Before testing in Lark, verify:
- the workflow has the correct `lark_app_id`
- the workflow has the correct `lark_app_secret`
- the Lark listener subscription is `active`
- the listener runtime becomes `healthy`
- the workflow version you are testing is the latest intended version

For the shared listener template, make sure the workflow version includes conditional routing from `START` so the Lark path does not execute WeCom nodes for Lark-triggered runs.

## Expected healthy behavior

When everything is configured correctly:
- the backend shows a Lark WebSocket connection
- sending a Lark message creates a workflow run
- the run trace contains `platform = "lark"` and `event_type = "im.message.receive_v1"`
- the reply node sends a message back to the same chat

## Troubleshooting

### Symptom: no Docker logs, no traces, no replies

Check these first:
- the app uses long connection, not webhook mode
- the published app version includes `im.message.receive_v1`
- the App ID and App Secret in Orcheo belong to the same published app
- the bot is visible to the user or chat you are testing from

If Orcheo shows a connected Lark runtime but no new runs are created, the problem is usually on the Lark app configuration side rather than in Orcheo runtime dispatch.

### Symptom: listener connects, but Orcheo reports the app is blocked

Check:
- the app was published after adding the event
- the event was added to the published version, not only a draft version
- the app is using the expected domain (`https://open.larksuite.com` for global Lark)

### Symptom: a run is created, but the workflow still fails

That means ingress is working and the failure is inside the workflow graph.

Common causes:
- another platform branch still runs due to graph wiring
- unrelated credentials referenced by the workflow are missing
- the send-message node is configured with the wrong credential or reply target

## Recommended verification sequence

When creating a new Lark app for Orcheo:
1. Create app
2. Enable bot capability
3. Add required message permissions
4. Enable long connection event subscription
5. Add `im.message.receive_v1`
6. Publish the app version
7. Configure App ID and App Secret in Orcheo
8. Confirm the listener runtime is healthy
9. Send a direct message to the bot
10. Verify that a workflow run is created before debugging reply behavior
