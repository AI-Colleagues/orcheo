import type { Workflow } from "../workflow-types";
import type { WorkflowTemplateDefinition } from "./template-definition";
import { TEMPLATE_OWNER } from "./template-owner";
import wecomLarkSharedListenerConfig from "./assets/wecom-lark-shared-listener/config.json";
import wecomLarkSharedListenerScript from "./assets/wecom-lark-shared-listener/workflow.py?raw";

const WECOM_LARK_SHARED_LISTENER_MERMAID = `---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>START</p>]):::first
	wecom_listener(wecom_listener)
	lark_listener(lark_listener)
	agent_reply(agent_reply)
	extract_reply(extract_reply)
	ws_reply_wecom(ws_reply_wecom)
	get_lark_tenant_token(get_lark_tenant_token)
	send_lark(send_lark)
	__end__([<p>END</p>]):::last
	__start__ -. &nbsp;lark&nbsp; .-> lark_listener;
	__start__ -. &nbsp;wecom&nbsp; .-> wecom_listener;
	agent_reply --> extract_reply;
	extract_reply -. &nbsp;lark&nbsp; .-> get_lark_tenant_token;
	extract_reply -. &nbsp;wecom&nbsp; .-> ws_reply_wecom;
	get_lark_tenant_token --> send_lark;
	lark_listener --> agent_reply;
	wecom_listener --> agent_reply;
	send_lark --> __end__;
	ws_reply_wecom --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
`;

export const WECOM_LARK_SHARED_LISTENER_WORKFLOW: Workflow = {
  id: "template-wecom-lark-shared-listener",
  name: "WeCom + Lark Shared Listener",
  description:
    "Uses plugin-provided WeCom and Lark listener nodes in one workflow, generates one shared AgentNode reply, and sends it through the matching channel branch.",
  createdAt: "2026-03-16T12:00:00Z",
  updatedAt: "2026-03-20T12:00:00Z",
  owner: TEMPLATE_OWNER,
  tags: ["template", "wecom", "lark", "listener", "plugin", "agent"],
  nodes: [
    {
      id: "start",
      type: "start",
      position: { x: 0, y: 120 },
      data: {
        label: "Start",
        type: "start",
        description: "Workflow entry point for plugin-listener runs.",
      },
    },
    {
      id: "wecom_listener",
      type: "trigger",
      position: { x: 230, y: 40 },
      data: {
        label: "WeComListenerPluginNode",
        type: "trigger",
        backendType: "WeComListenerPluginNode",
        description:
          "Receives WeCom events through the external listener plugin.",
      },
    },
    {
      id: "lark_listener",
      type: "trigger",
      position: { x: 230, y: 200 },
      data: {
        label: "LarkListenerPluginNode",
        type: "trigger",
        backendType: "LarkListenerPluginNode",
        description:
          "Receives Lark events through the external listener plugin.",
      },
    },
    {
      id: "agent_reply",
      type: "agent",
      position: { x: 520, y: 120 },
      data: {
        label: "AgentNode",
        type: "agent",
        description:
          "Generates one shared reply for both WeCom and Lark listener events.",
      },
    },
    {
      id: "extract_reply",
      type: "utility",
      position: { x: 760, y: 120 },
      data: {
        label: "AgentReplyExtractorNode",
        type: "utility",
        backendType: "AgentReplyExtractorNode",
        description:
          "Extracts the final assistant reply text for downstream send nodes.",
      },
    },
    {
      id: "reply_route",
      type: "condition",
      position: { x: 1030, y: 120 },
      data: {
        label: "SwitchNode",
        type: "condition",
        backendType: "SwitchNode",
        description:
          "Routes the extracted reply into the matching platform send branch.",
        value: "{{inputs.platform}}",
        defaultBranchKey: "wecom",
        cases: [
          { match: "wecom", branchKey: "wecom", label: "WeCom" },
          { match: "lark", branchKey: "lark", label: "Lark" },
        ],
      },
    },
    {
      id: "ws_reply_wecom",
      type: "messaging",
      position: { x: 1300, y: 20 },
      data: {
        label: "WeComWsReplyNode",
        type: "messaging",
        backendType: "WeComWsReplyNode",
        description:
          "Replies to the WeCom message via the WebSocket long-connection.",
      },
    },
    {
      id: "get_lark_tenant_token",
      type: "utility",
      position: { x: 1300, y: 220 },
      data: {
        label: "LarkTenantAccessTokenNode",
        type: "utility",
        backendType: "LarkTenantAccessTokenNode",
        description:
          "Fetches a Lark access token with the standard Lark auth node.",
      },
    },
    {
      id: "send_lark",
      type: "messaging",
      position: { x: 1570, y: 220 },
      data: {
        label: "LarkSendMessageNode",
        type: "messaging",
        backendType: "LarkSendMessageNode",
        description:
          "Replies to the inbound Lark message using the Lark message API.",
      },
    },
    {
      id: "end",
      type: "end",
      position: { x: 1840, y: 120 },
      data: {
        label: "Finish",
        type: "end",
        description: "The shared plugin-listener reply flow completes here.",
      },
    },
  ],
  edges: [
    { id: "edge-start-wecom", source: "start", target: "wecom_listener" },
    { id: "edge-start-lark", source: "start", target: "lark_listener" },
    {
      id: "edge-wecom-agent",
      source: "wecom_listener",
      target: "agent_reply",
    },
    {
      id: "edge-lark-agent",
      source: "lark_listener",
      target: "agent_reply",
    },
    {
      id: "edge-agent-extract",
      source: "agent_reply",
      target: "extract_reply",
    },
    {
      id: "edge-extract-route",
      source: "extract_reply",
      target: "reply_route",
    },
    {
      id: "edge-route-wecom-reply",
      source: "reply_route",
      target: "ws_reply_wecom",
      sourceHandle: "wecom",
    },
    {
      id: "edge-route-lark-token",
      source: "reply_route",
      target: "get_lark_tenant_token",
      sourceHandle: "lark",
    },
    {
      id: "edge-lark-token-send",
      source: "get_lark_tenant_token",
      target: "send_lark",
    },
    { id: "edge-wecom-end", source: "ws_reply_wecom", target: "end" },
    { id: "edge-lark-end", source: "send_lark", target: "end" },
  ],
  versions: [
    {
      id: "template-wecom-lark-shared-listener-v1",
      mermaid: WECOM_LARK_SHARED_LISTENER_MERMAID,
    },
  ],
};

export const WECOM_LARK_SHARED_LISTENER_TEMPLATE: WorkflowTemplateDefinition = {
  workflow: WECOM_LARK_SHARED_LISTENER_WORKFLOW,
  script: wecomLarkSharedListenerScript,
  runnableConfig: wecomLarkSharedListenerConfig,
  notes:
    "Seeded from the shared WeCom and Lark listener plugin reply template.",
  metadata: {
    templateVersion: "1.0.1",
    minOrcheoVersion: "0.1.0",
    validatedProviderApi: "wecom-lark-listener-plugin-suite-2026-03-16",
    validationDate: "2026-03-20",
    owner: "Shaojie Jiang",
    requiredPlugins: [
      "orcheo-plugin-wecom-listener",
      "orcheo-plugin-lark-listener",
    ],
    acceptanceCriteria: [
      "Imports into Canvas once both listener plugins are installed.",
      "Compiles valid WeCom and Lark listener subscriptions from one workflow.",
      "Runs both listeners through one shared AgentNode reply path before branching by platform.",
      "Routes WeCom replies via WebSocket long-connection and Lark replies via HTTP API.",
    ],
    revalidationTriggers: [
      "WeCom or Lark listener plugin contract change",
      "WeComWsReplyNode, LarkTenantAccessTokenNode, or Lark OpenAPI send contract change",
      "Shared listener payload contract change",
      "Canvas template import contract change",
    ],
  },
};
