import type { Workflow } from "../workflow-types";
import type { WorkflowTemplateDefinition } from "./template-definition";
import { TEMPLATE_OWNER } from "./template-owner";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";
import vibeAgentConfig from "./assets/vibe-agent/config.json";
import vibeAgentScript from "./assets/vibe-agent/workflow.py?raw";

const VIBE_AGENT_MERMAID = `---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>START</p>]):::first
	prepare_prompt(prepare_prompt)
	claude_code_agent(claude_code_agent)
	codex_agent(codex_agent)
	gemini_agent(gemini_agent)
	extract_reply(extract_reply)
	__end__([<p>END</p>]):::last
	__start__ --> prepare_prompt;
	claude_code_agent --> extract_reply;
	codex_agent --> extract_reply;
	gemini_agent --> extract_reply;
	prepare_prompt -. &nbsp;claude_code&nbsp; .-> claude_code_agent;
	prepare_prompt -. &nbsp;codex&nbsp; .-> codex_agent;
	prepare_prompt -. &nbsp;gemini&nbsp; .-> gemini_agent;
	extract_reply --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
`;

export const VIBE_AGENT_WORKFLOW: Workflow = {
  id: "template-vibe-agent",
  handle: VIBE_WORKFLOW_HANDLE,
  name: "Orcheo Vibe",
  description:
    "Routes ChatKit conversations to the connected external agent runtime selected in the native ChatKit model picker.",
  createdAt: "2026-04-13T09:00:00Z",
  updatedAt: "2026-04-13T09:00:00Z",
  owner: TEMPLATE_OWNER,
  tags: ["template", "vibe", "agent", "external-agent"],
  nodes: [
    {
      id: "start",
      type: "start",
      position: { x: 0, y: 0 },
      data: {
        label: "Start",
        type: "start",
        description: "Entry point for chat history and workflow inputs.",
      },
    },
    {
      id: "claude_code_agent",
      type: "agent",
      position: { x: 260, y: -140 },
      data: {
        label: "ClaudeCodeNode",
        type: "agent",
        description: "Runs Claude Code for the selected Vibe conversation.",
      },
    },
    {
      id: "codex_agent",
      type: "agent",
      position: { x: 260, y: 0 },
      data: {
        label: "CodexNode",
        type: "agent",
        description: "Runs Codex for the selected Vibe conversation.",
      },
    },
    {
      id: "gemini_agent",
      type: "agent",
      position: { x: 260, y: 140 },
      data: {
        label: "GeminiNode",
        type: "agent",
        description: "Runs Gemini CLI for the selected Vibe conversation.",
      },
    },
    {
      id: "end",
      type: "end",
      position: { x: 520, y: 0 },
      data: {
        label: "Finish",
        type: "end",
        description: "Workflow completes after the selected agent replies.",
      },
    },
  ],
  edges: [
    {
      id: "edge-start-claude-code-agent",
      source: "start",
      target: "claude_code_agent",
    },
    {
      id: "edge-start-codex-agent",
      source: "start",
      target: "codex_agent",
    },
    {
      id: "edge-start-gemini-agent",
      source: "start",
      target: "gemini_agent",
    },
    {
      id: "edge-claude-code-agent-end",
      source: "claude_code_agent",
      target: "end",
    },
    {
      id: "edge-codex-agent-end",
      source: "codex_agent",
      target: "end",
    },
    {
      id: "edge-gemini-agent-end",
      source: "gemini_agent",
      target: "end",
    },
  ],
  versions: [
    {
      id: "template-vibe-agent-v1",
      mermaid: VIBE_AGENT_MERMAID,
    },
  ],
};

export const VIBE_AGENT_TEMPLATE: WorkflowTemplateDefinition = {
  workflow: VIBE_AGENT_WORKFLOW,
  script: vibeAgentScript,
  runnableConfig: vibeAgentConfig,
  notes: "Seeded from the Orcheo Vibe template.",
  metadata: {
    templateVersion: "1.0.1",
    minOrcheoVersion: "0.14.2",
    validatedProviderApi: "private-bot-listener-suite-2026-03-11",
    validationDate: "2026-04-13",
    owner: TEMPLATE_OWNER.name,
    acceptanceCriteria: [
      "Routes Vibe ChatKit requests to the selected external agent provider.",
      "Includes Canvas page context in the generated external-agent prompt.",
    ],
    revalidationTriggers: [
      "ChatKit metadata payload shape changes.",
      "External agent provider selection contract changes.",
    ],
  },
};
