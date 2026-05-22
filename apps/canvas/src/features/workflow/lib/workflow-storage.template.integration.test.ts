import { afterEach, describe, expect, it, vi } from "vitest";

import {
  WORKFLOW_STORAGE_EVENT,
  createWorkflowFromTemplate,
} from "./workflow-storage";
import { setCandidateBadges } from "@features/workflow/data/templates/candidate-badges";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";

const { getFetchMock, queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

afterEach(() => {
  setCandidateBadges([]);
});

// Minimal scripts that satisfy per-test content assertions.
const PYTHON_AGENT_SCRIPT =
  "from orcheo.nodes.ai import AgentNode\ngraph = AgentNode()\n";
const TELEGRAM_AGENT_SCRIPT = [
  "from orcheo.nodes.telegram import MessageTelegramNode",
  "workflow_tools = []",
].join("\n");
const TELEGRAM_HEARTBEAT_SCRIPT = [
  "from orcheo.nodes.triggers import CronTriggerNode",
  "from orcheo.nodes.telegram import MessageTelegramNode",
  "# * * * * *",
  "# allow_overlapping=True",
].join("\n");
const TELEGRAM_PRIVATE_LISTENER_SCRIPT =
  "from orcheo.nodes.telegram import TelegramBotListenerNode\n";
const DISCORD_PRIVATE_LISTENER_SCRIPT =
  "from orcheo.nodes.discord import DiscordBotListenerNode, MessageDiscordNode\n";
const QQ_PRIVATE_LISTENER_SCRIPT =
  "from orcheo.nodes.qq import QQBotListenerNode, MessageQQNode\n";
const PRIVATE_BOT_SHARED_LISTENER_SCRIPT = [
  "from orcheo.nodes.telegram import TelegramBotListenerNode",
  "from orcheo.nodes.discord import DiscordBotListenerNode",
  "from orcheo.nodes.qq import QQBotListenerNode",
  "SwitchEdge(",
].join("\n");
const WECOM_LARK_SHARED_LISTENER_SCRIPT = [
  "AgentNode",
  "AgentReplyExtractorNode",
  "WeComWsReplyNode",
  "LarkSendMessageNode",
  "LarkTenantAccessTokenNode",
].join("\n");
const GEMINI_SCRIPT =
  "from orcheo.nodes.ai.external.gemini import GeminiNode\n";

describe("workflow-storage API integration - template creation", () => {
  it("creates a workflow and ingests a Python version from template", async () => {
    setCandidateBadges([
      {
        id: "template-python-agent",
        handle: "python-agent",
        name: "Simple Agent",
        script: PYTHON_AGENT_SCRIPT,
        notes: "Seeded from Simple Agent template (`agent.py`).",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-1",
        name: "Simple Agent",
        slug: "workflow-template-1",
        description: "A single-node agent workflow seeded from `agent.py`.",
        tags: ["python", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-1-version-1",
        workflow_id: "workflow-template-1",
        version: 1,
        graph: {
          format: "langgraph-script",
          source:
            "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-python-agent",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-1",
        name: "Simple Agent",
        slug: "workflow-template-1",
        description: "A single-node agent workflow seeded from `agent.py`.",
        tags: ["python", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-1-version-1",
          workflow_id: "workflow-template-1",
          version: 1,
          graph: {
            format: "langgraph-script",
            source:
              "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-python-agent",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const listener = vi.fn();
    window.addEventListener(WORKFLOW_STORAGE_EVENT, listener);

    const created = await createWorkflowFromTemplate("template-python-agent");

    expect(created?.id).toBe("workflow-template-1");
    expect(created?.versions).toHaveLength(1);
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(WORKFLOW_STORAGE_EVENT, listener);

    expect(mockFetch).toHaveBeenCalledTimes(5);
    expect(String(mockFetch.mock.calls[2]?.[0])).toContain(
      "/api/workflows/workflow-template-1/versions/ingest",
    );

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as { script?: string; metadata?: { source?: string } };
    expect(ingestBody.script).toContain(
      "from orcheo.nodes.ai import AgentNode",
    );
    expect(ingestBody.metadata?.source).toBe("canvas-template");
  });

  it("creates the vibe workflow with a stable handle", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-vibe-1",
        handle: VIBE_WORKFLOW_HANDLE,
        name: "Orcheo Vibe",
        slug: "workflow-vibe-1",
        description: "Routes ChatKit conversations to external agent runtimes.",
        tags: ["orcheo-vibe-agent", "external-agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-vibe-1-version-1",
        workflow_id: "workflow-vibe-1",
        version: 1,
        graph: {
          format: "langgraph-script",
          source:
            "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-vibe-agent",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-vibe-1",
        handle: VIBE_WORKFLOW_HANDLE,
        name: "Orcheo Vibe",
        slug: "workflow-vibe-1",
        description: "Routes ChatKit conversations to external agent runtimes.",
        tags: ["orcheo-vibe-agent", "external-agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-vibe-1-version-1",
          workflow_id: "workflow-vibe-1",
          version: 1,
          graph: {
            format: "langgraph-script",
            source:
              "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-vibe-agent",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const created = await createWorkflowFromTemplate("template-vibe-agent", {
      name: "Orcheo Vibe",
      handle: VIBE_WORKFLOW_HANDLE,
      tags: ["orcheo-vibe-agent", "external-agent"],
    });
    expect(created).toBeDefined();

    const createCall = mockFetch.mock.calls.find(
      ([path, options]) =>
        String(path).includes("/api/workflows") &&
        options?.method === "POST" &&
        !String(path).includes("/versions/ingest"),
    );
    const createBody = JSON.parse(String(createCall?.[1]?.body ?? "{}")) as {
      handle?: string | null;
    };
    expect(createBody.handle).toBe(VIBE_WORKFLOW_HANDLE);
  });

  it("starts numeric suffixes at 1 and ignores archived workflow names", async () => {
    setCandidateBadges([
      {
        id: "template-python-agent",
        handle: "python-agent",
        name: "Simple Agent",
        script: PYTHON_AGENT_SCRIPT,
        notes: "Seeded from Simple Agent template (`agent.py`).",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([
        {
          id: "workflow-existing-base",
          name: "Simple Agent",
          slug: "workflow-existing-base",
          description: "Existing active workflow.",
          tags: ["python", "agent"],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
        {
          id: "workflow-existing-two",
          name: "Simple Agent 2",
          slug: "workflow-existing-two",
          description: "Existing active workflow with suffix.",
          tags: ["python", "agent"],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
        {
          id: "workflow-archived-one",
          name: "Simple Agent 1",
          slug: "workflow-archived-one",
          description: "Archived workflow should not block suffix reuse.",
          tags: ["python", "agent"],
          is_archived: true,
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse({
        id: "workflow-template-1b",
        name: "Simple Agent 1",
        slug: "workflow-template-1b",
        description: "A single-node agent workflow seeded from `agent.py`.",
        tags: ["python", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-1b-version-1",
        workflow_id: "workflow-template-1b",
        version: 1,
        graph: {
          format: "langgraph-script",
          source:
            "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-python-agent",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-1b",
        name: "Simple Agent 1",
        slug: "workflow-template-1b",
        description: "A single-node agent workflow seeded from `agent.py`.",
        tags: ["python", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-1b-version-1",
          workflow_id: "workflow-template-1b",
          version: 1,
          graph: {
            format: "langgraph-script",
            source:
              "from langgraph.graph import StateGraph\nfrom orcheo.graph.state import State\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-python-agent",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-python-agent");

    const createBody = JSON.parse(
      String(mockFetch.mock.calls[1]?.[1]?.body ?? "{}"),
    ) as { name?: string };

    expect(createBody.name).toBe("Simple Agent 1");
  });

  it("includes runnable config when template provides one", async () => {
    setCandidateBadges([
      {
        id: "template-mongodb-qa-agent",
        handle: "mongodb-qa-agent",
        name: "MongoDB QA Agent",
        script: "# mongodb qa agent script\n",
        config: {
          configurable: {
            database: "my_database",
            collection: "my_collection",
          },
        },
        notes: "MongoDB QA template.",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-2",
        name: "MongoDB QA Agent",
        slug: "workflow-template-2",
        description: "MongoDB QA agent template.",
        tags: ["python", "agent", "mongodb"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-2-version-1",
        workflow_id: "workflow-template-2",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-mongodb-qa-agent",
        },
        runnable_config: {
          configurable: {
            database: "my_database",
            collection: "my_collection",
          },
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-2",
        name: "MongoDB QA Agent",
        slug: "workflow-template-2",
        description: "MongoDB QA agent template.",
        tags: ["python", "agent", "mongodb"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-2-version-1",
          workflow_id: "workflow-template-2",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-mongodb-qa-agent",
          },
          runnable_config: {
            configurable: {
              database: "my_database",
              collection: "my_collection",
            },
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const created = await createWorkflowFromTemplate(
      "template-mongodb-qa-agent",
    );

    expect(created?.id).toBe("workflow-template-2");
    expect(mockFetch).toHaveBeenCalledTimes(5);

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as { runnable_config?: { configurable?: { database?: string } } };
    expect(ingestBody.runnable_config?.configurable?.database).toBe(
      "my_database",
    );
  });

  it("creates a workflow from the Gemini external-agent template", async () => {
    setCandidateBadges([
      {
        id: "template-gemini-agent",
        handle: "gemini-agent",
        name: "Gemini Agent",
        script: GEMINI_SCRIPT,
        config: { configurable: { working_directory: "/workspace/agents" } },
        notes: "Gemini external-agent template.",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-gemini",
        name: "Gemini Agent",
        slug: "workflow-template-gemini",
        description: "Gemini external-agent template.",
        tags: ["gemini", "agent", "external-agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-gemini-version-1",
        workflow_id: "workflow-template-gemini",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-gemini-agent",
        },
        runnable_config: {
          configurable: {
            working_directory: "/workspace/agents",
          },
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-gemini",
        name: "Gemini Agent",
        slug: "workflow-template-gemini",
        description: "Gemini external-agent template.",
        tags: ["gemini", "agent", "external-agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-gemini-version-1",
          workflow_id: "workflow-template-gemini",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-gemini-agent",
          },
          runnable_config: {
            configurable: {
              working_directory: "/workspace/agents",
            },
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const created = await createWorkflowFromTemplate("template-gemini-agent");

    expect(created?.id).toBe("workflow-template-gemini");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      script?: string;
      metadata?: { source?: string; template_id?: string };
      runnable_config?: { configurable?: { working_directory?: string } };
    };
    expect(ingestBody.script).toContain(
      "from orcheo.nodes.ai.external.gemini import GeminiNode",
    );
    expect(ingestBody.metadata?.template_id).toBe("template-gemini-agent");
    expect(ingestBody.runnable_config?.configurable?.working_directory).toBe(
      "/workspace/agents",
    );
  });

  it("creates the Telegram agent template with agent-driven Telegram delivery", async () => {
    setCandidateBadges([
      {
        id: "template-telegram-agent",
        handle: "telegram-agent",
        name: "Telegram Agent Sender",
        script: TELEGRAM_AGENT_SCRIPT,
        config: {
          configurable: {
            ai_model: "openai:gpt-4o-mini",
            system_prompt: "Reply to this Telegram message.",
          },
        },
        notes: "Telegram agent template.",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-3",
        name: "Telegram Agent Sender",
        slug: "workflow-template-3",
        description: "Telegram agent sender template.",
        tags: ["python", "agent", "telegram"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-3-version-1",
        workflow_id: "workflow-template-3",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-telegram-agent",
        },
        runnable_config: {
          configurable: {
            ai_model: "openai:gpt-4o-mini",
          },
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-3",
        name: "Telegram Agent Sender",
        slug: "workflow-template-3",
        description: "Telegram agent sender template.",
        tags: ["python", "agent", "telegram"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-3-version-1",
          workflow_id: "workflow-template-3",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-telegram-agent",
          },
          runnable_config: {
            configurable: {
              ai_model: "openai:gpt-4o-mini",
            },
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const created = await createWorkflowFromTemplate("template-telegram-agent");

    expect(created?.id).toBe("workflow-template-3");
    expect(mockFetch).toHaveBeenCalledTimes(5);

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      script?: string;
      runnable_config?: {
        configurable?: { ai_model?: string; system_prompt?: string };
      };
    };
    expect(ingestBody.script).toContain("workflow_tools");
    expect(ingestBody.script).toContain("MessageTelegramNode");
    expect(ingestBody.runnable_config?.configurable?.ai_model).toBe(
      "openai:gpt-4o-mini",
    );
    expect(ingestBody.runnable_config?.configurable?.system_prompt).toContain(
      "Telegram message",
    );
  });

  it("creates the Telegram heartbeat template with cron-triggered delivery", async () => {
    setCandidateBadges([
      {
        id: "template-telegram-heartbeat",
        handle: "telegram-heartbeat",
        name: "Telegram Heartbeat",
        script: TELEGRAM_HEARTBEAT_SCRIPT,
        config: {
          configurable: { heartbeat_message: "Heartbeat: workflow is alive." },
        },
        notes: "Telegram heartbeat template.",
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-4",
        name: "Telegram Heartbeat",
        slug: "workflow-template-4",
        description: "Telegram heartbeat template.",
        tags: ["python", "telegram", "trigger"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-4-version-1",
        workflow_id: "workflow-template-4",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: {
            cron: [
              {
                expression: "* * * * *",
                timezone: "UTC",
                allow_overlapping: true,
              },
            ],
          },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-telegram-heartbeat",
        },
        runnable_config: {
          configurable: {
            heartbeat_message: "Heartbeat: workflow is alive.",
          },
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-4",
        name: "Telegram Heartbeat",
        slug: "workflow-template-4",
        description: "Telegram heartbeat template.",
        tags: ["python", "telegram", "trigger"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-4-version-1",
          workflow_id: "workflow-template-4",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: {
              cron: [
                {
                  expression: "* * * * *",
                  timezone: "UTC",
                  allow_overlapping: true,
                },
              ],
            },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-telegram-heartbeat",
          },
          runnable_config: {
            configurable: {
              heartbeat_message: "Heartbeat: workflow is alive.",
            },
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const created = await createWorkflowFromTemplate(
      "template-telegram-heartbeat",
    );

    expect(created?.id).toBe("workflow-template-4");
    expect(mockFetch).toHaveBeenCalledTimes(5);

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      script?: string;
      metadata?: {
        canvas?: {
          snapshot?: { name?: string };
        };
      };
      runnable_config?: {
        configurable?: { heartbeat_message?: string };
      };
    };
    expect(ingestBody.script).toContain("CronTriggerNode");
    expect(ingestBody.script).toContain("MessageTelegramNode");
    expect(ingestBody.script).toContain("* * * * *");
    expect(ingestBody.script).toContain("allow_overlapping=True");
    expect(ingestBody.metadata?.canvas?.snapshot?.name).toBe(
      "Telegram Heartbeat",
    );
    expect(ingestBody.runnable_config?.configurable?.heartbeat_message).toBe(
      "Heartbeat: workflow is alive.",
    );
  });

  it("includes validation metadata for the private Telegram listener template", async () => {
    setCandidateBadges([
      {
        id: "template-telegram-private-listener",
        handle: "telegram-private-listener",
        name: "Telegram Private Listener",
        script: TELEGRAM_PRIVATE_LISTENER_SCRIPT,
        notes: "Telegram private listener template.",
        rawMetadata: {
          template_version: "1.0.0",
          validated_provider_api: "telegram-bot-api",
        },
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-5",
        name: "Telegram Private Listener",
        slug: "workflow-template-5",
        description: "Telegram private listener template.",
        tags: ["telegram", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-5-version-1",
        workflow_id: "workflow-template-5",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [], listeners: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-telegram-private-listener",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-5",
        name: "Telegram Private Listener",
        slug: "workflow-template-5",
        description: "Telegram private listener template.",
        tags: ["telegram", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-5-version-1",
          workflow_id: "workflow-template-5",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [], listeners: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-telegram-private-listener",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-telegram-private-listener");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      metadata?: {
        template?: {
          templateVersion?: string;
          validatedProviderApi?: string;
        };
      };
      script?: string;
    };

    expect(ingestBody.script).toContain("TelegramBotListenerNode");
    expect(ingestBody.metadata?.template?.templateVersion).toBe("1.0.0");
    expect(ingestBody.metadata?.template?.validatedProviderApi).toBe(
      "telegram-bot-api",
    );
  });

  it("includes validation metadata for the private Discord listener template", async () => {
    setCandidateBadges([
      {
        id: "template-discord-private-listener",
        handle: "discord-private-listener",
        name: "Discord Private Listener",
        script: DISCORD_PRIVATE_LISTENER_SCRIPT,
        notes: "Discord private listener template.",
        rawMetadata: {
          template_version: "1.0.0",
          validated_provider_api: "discord-gateway-v10",
        },
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-6",
        name: "Discord Private Listener",
        slug: "workflow-template-6",
        description: "Discord private listener template.",
        tags: ["discord", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-6-version-1",
        workflow_id: "workflow-template-6",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [], listeners: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-discord-private-listener",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-6",
        name: "Discord Private Listener",
        slug: "workflow-template-6",
        description: "Discord private listener template.",
        tags: ["discord", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-6-version-1",
          workflow_id: "workflow-template-6",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [], listeners: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-discord-private-listener",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-discord-private-listener");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      metadata?: {
        template?: {
          templateVersion?: string;
          validatedProviderApi?: string;
        };
      };
      script?: string;
    };

    expect(ingestBody.script).toContain("DiscordBotListenerNode");
    expect(ingestBody.script).toContain("MessageDiscordNode");
    expect(ingestBody.metadata?.template?.templateVersion).toBe("1.0.0");
    expect(ingestBody.metadata?.template?.validatedProviderApi).toBe(
      "discord-gateway-v10",
    );
  });

  it("includes validation metadata for the private QQ listener template", async () => {
    setCandidateBadges([
      {
        id: "template-qq-private-listener",
        handle: "qq-private-listener",
        name: "QQ Private Listener",
        script: QQ_PRIVATE_LISTENER_SCRIPT,
        notes: "QQ private listener template.",
        rawMetadata: {
          template_version: "1.0.0",
          validated_provider_api: "qq-bot-api-v2",
          reply_node_contracts: ["MessageQQNode@1"],
        },
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-7",
        name: "QQ Private Listener",
        slug: "workflow-template-7",
        description: "QQ private listener template.",
        tags: ["qq", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-7-version-1",
        workflow_id: "workflow-template-7",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [], listeners: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-qq-private-listener",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-7",
        name: "QQ Private Listener",
        slug: "workflow-template-7",
        description: "QQ private listener template.",
        tags: ["qq", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-7-version-1",
          workflow_id: "workflow-template-7",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [], listeners: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-qq-private-listener",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-qq-private-listener");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      metadata?: {
        template?: {
          templateVersion?: string;
          validatedProviderApi?: string;
          replyNodeContracts?: string[];
        };
      };
      script?: string;
    };

    expect(ingestBody.script).toContain("QQBotListenerNode");
    expect(ingestBody.script).toContain("MessageQQNode");
    expect(ingestBody.metadata?.template?.templateVersion).toBe("1.0.0");
    expect(ingestBody.metadata?.template?.validatedProviderApi).toBe(
      "qq-bot-api-v2",
    );
    expect(ingestBody.metadata?.template?.replyNodeContracts).toEqual([
      "MessageQQNode@1",
    ]);
  });

  it("includes routing metadata for the shared private listener template", async () => {
    setCandidateBadges([
      {
        id: "template-private-bot-shared-listener",
        handle: "private-bot-shared-listener",
        name: "Private Bot Shared Listener",
        script: PRIVATE_BOT_SHARED_LISTENER_SCRIPT,
        notes: "Shared private bot listener template.",
        rawMetadata: {
          validated_provider_api: "private-bot-listener-suite-2026-03-11",
          reply_node_contracts: [
            "MessageTelegramNode@1",
            "MessageDiscordNode@1",
            "MessageQQNode@1",
          ],
        },
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-8",
        name: "Private Bot Shared Listener",
        slug: "workflow-template-8",
        description: "Shared private listener template.",
        tags: ["telegram", "discord", "qq", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-8-version-1",
        workflow_id: "workflow-template-8",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [], listeners: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-private-bot-shared-listener",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-8",
        name: "Private Bot Shared Listener",
        slug: "workflow-template-8",
        description: "Shared private listener template.",
        tags: ["telegram", "discord", "qq", "listener", "agent"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-8-version-1",
          workflow_id: "workflow-template-8",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [], listeners: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-private-bot-shared-listener",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-private-bot-shared-listener");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      metadata?: {
        template?: {
          replyNodeContracts?: string[];
          validatedProviderApi?: string;
        };
      };
      script?: string;
    };

    expect(ingestBody.script).toContain("TelegramBotListenerNode");
    expect(ingestBody.script).toContain("DiscordBotListenerNode");
    expect(ingestBody.script).toContain("QQBotListenerNode");
    expect(ingestBody.script).toContain("SwitchEdge(");
    expect(ingestBody.metadata?.template?.validatedProviderApi).toBe(
      "private-bot-listener-suite-2026-03-11",
    );
    expect(ingestBody.metadata?.template?.replyNodeContracts).toEqual([
      "MessageTelegramNode@1",
      "MessageDiscordNode@1",
      "MessageQQNode@1",
    ]);
  });

  it("includes shared agent routing for the WeCom and Lark listener plugin template", async () => {
    setCandidateBadges([
      {
        id: "template-wecom-lark-shared-listener",
        handle: "wecom-lark-shared-listener",
        name: "WeCom + Lark Shared Listener",
        script: WECOM_LARK_SHARED_LISTENER_SCRIPT,
        config: {
          configurable: {
            ai_model: "openai:gpt-4.1-mini",
            operator_note: "Requires listener plugins installed.",
          },
        },
        notes: "WeCom and Lark shared listener template.",
        rawMetadata: {
          template_version: "1.0.1",
          validated_provider_api: "wecom-lark-listener-plugin-suite-2026-03-16",
          required_plugins: [
            "orcheo-plugin-wecom-listener",
            "orcheo-plugin-lark-listener",
          ],
        },
      },
    ]);
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse({
        plugins: [
          {
            name: "orcheo-plugin-wecom-listener",
            enabled: true,
            status: "installed",
            version: "0.1.0",
            exports: ["nodes", "listeners"],
            loaded: true,
            load_error: null,
          },
          {
            name: "orcheo-plugin-lark-listener",
            enabled: true,
            status: "installed",
            version: "0.1.0",
            exports: ["nodes", "listeners"],
            loaded: true,
            load_error: null,
          },
        ],
      }),
      jsonResponse([]),
      jsonResponse({
        id: "workflow-template-9",
        name: "WeCom + Lark Shared Listener",
        slug: "workflow-template-9",
        description: "Shared WeCom and Lark listener template.",
        tags: ["wecom", "lark", "listener", "agent", "plugin"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-9-version-1",
        workflow_id: "workflow-template-9",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
          entrypoint: null,
          index: { cron: [], listeners: [] },
        },
        metadata: {
          source: "canvas-template",
          template_id: "template-wecom-lark-shared-listener",
        },
        notes: "Template ingest",
        created_by: "canvas-app",
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse({
        id: "workflow-template-9",
        name: "WeCom + Lark Shared Listener",
        slug: "workflow-template-9",
        description: "Shared WeCom and Lark listener template.",
        tags: ["wecom", "lark", "listener", "agent", "plugin"],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          id: "workflow-template-9-version-1",
          workflow_id: "workflow-template-9",
          version: 1,
          graph: {
            format: "langgraph-script",
            source: "from langgraph.graph import StateGraph\n",
            entrypoint: null,
            index: { cron: [], listeners: [] },
          },
          metadata: {
            source: "canvas-template",
            template_id: "template-wecom-lark-shared-listener",
          },
          notes: "Template ingest",
          created_by: "canvas-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    await createWorkflowFromTemplate("template-wecom-lark-shared-listener");

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[3]?.[1]?.body ?? "{}"),
    ) as {
      metadata?: {
        template?: {
          templateVersion?: string;
          validatedProviderApi?: string;
        };
      };
      runnable_config?: {
        configurable?: { ai_model?: string; operator_note?: string };
      };
      script?: string;
    };

    expect(ingestBody.script).toContain("AgentNode");
    expect(ingestBody.script).toContain("AgentReplyExtractorNode");
    expect(ingestBody.script).toContain("WeComWsReplyNode");
    expect(ingestBody.script).toContain("LarkSendMessageNode");
    expect(ingestBody.script).toContain("LarkTenantAccessTokenNode");
    expect(ingestBody.metadata?.template?.templateVersion).toBe("1.0.1");
    expect(ingestBody.metadata?.template?.validatedProviderApi).toBe(
      "wecom-lark-listener-plugin-suite-2026-03-16",
    );
    expect(ingestBody.runnable_config?.configurable?.ai_model).toBe(
      "openai:gpt-4.1-mini",
    );
    expect(ingestBody.runnable_config?.configurable?.operator_note).toContain(
      "listener plugins",
    );
  });

  it("fails fast when a plugin-backed template is missing required plugins", async () => {
    setCandidateBadges([
      {
        id: "template-wecom-lark-shared-listener",
        handle: "wecom-lark-shared-listener",
        name: "WeCom + Lark Shared Listener",
        script: WECOM_LARK_SHARED_LISTENER_SCRIPT,
        notes: "WeCom and Lark shared listener template.",
        rawMetadata: {
          required_plugins: [
            "orcheo-plugin-wecom-listener",
            "orcheo-plugin-lark-listener",
          ],
        },
      },
    ]);
    queueResponses([
      jsonResponse({
        plugins: [
          {
            name: "orcheo-plugin-wecom-listener",
            enabled: true,
            status: "installed",
            version: "0.1.0",
            exports: ["nodes", "listeners"],
            loaded: true,
            load_error: null,
          },
        ],
      }),
    ]);

    await expect(
      createWorkflowFromTemplate("template-wecom-lark-shared-listener"),
    ).rejects.toThrow(
      "Install required plugins before using this template: orcheo-plugin-lark-listener",
    );
  });
});
