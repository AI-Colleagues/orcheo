import { describe, expect, it, vi } from "vitest";

import {
  WORKFLOW_STORAGE_EVENT,
  createWorkflowFromTemplate,
} from "./workflow-storage";
import {
  getFetchMock,
  jsonResponse,
  queueResponses,
  setupFetchMock,
} from "./workflow-storage.test-helpers";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";

setupFetchMock();

describe("workflow-storage API integration - template creation", () => {
  it("creates a workflow and ingests a Python version from template", async () => {
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
    const createBody = JSON.parse(
      String(createCall?.[1]?.body ?? "{}"),
    ) as { handle?: string | null };
    expect(createBody.handle).toBe(VIBE_WORKFLOW_HANDLE);
  });

  it("starts numeric suffixes at 1 and ignores archived workflow names", async () => {
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
      "from orcheo.nodes.gemini import GeminiNode",
    );
    expect(ingestBody.metadata?.template_id).toBe("template-gemini-agent");
    expect(ingestBody.runnable_config?.configurable?.working_directory).toBe(
      "/workspace/agents",
    );
  });

  it("creates the Telegram agent template with agent-driven Telegram delivery", async () => {
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
    expect(created?.nodes.some((node) => node.id === "cron_trigger")).toBe(
      true,
    );
    expect(mockFetch).toHaveBeenCalledTimes(5);

    const ingestBody = JSON.parse(
      String(mockFetch.mock.calls[2]?.[1]?.body ?? "{}"),
    ) as {
      script?: string;
      metadata?: {
        canvas?: {
          snapshot?: {
            name?: string;
            nodes?: Array<{ id?: string }>;
          };
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
    expect(
      ingestBody.metadata?.canvas?.snapshot?.nodes?.some(
        (node) => node.id === "cron_trigger",
      ),
    ).toBe(true);
    expect(ingestBody.runnable_config?.configurable?.heartbeat_message).toBe(
      "Heartbeat: workflow is alive.",
    );
  });

  it("includes validation metadata for the private Telegram listener template", async () => {
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
    expect(ingestBody.script).toContain("Switch(");
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
