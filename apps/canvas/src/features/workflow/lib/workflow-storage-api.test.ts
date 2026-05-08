import { describe, expect, it } from "vitest";
import {
  extractCronConfigFromVersionGraph,
  fetchWorkflowRemediations,
  fetchWorkflowCanvasData,
  fetchWorkflowCredentialReadiness,
  fetchWorkflowListenerMetrics,
  fetchWorkflowListeners,
  fetchSystemPlugins,
  pauseWorkflowListener,
  resolveWorkflowShareUrl,
  resumeWorkflowListener,
  scheduleWorkflowFromLatestVersion,
  selectLatestWorkflowVersion,
  triggerWorkflowRun,
} from "./workflow-storage-api";
import {
  getFetchMock,
  jsonResponse,
  queueResponses,
  setupFetchMock,
} from "./workflow-storage.test-helpers";

setupFetchMock();

describe("workflow-storage-api helpers", () => {
  it("uses explicit share_url for published workflows", () => {
    const shareUrl = resolveWorkflowShareUrl({
      id: "wf-1",
      handle: "my-flow",
      is_public: true,
      share_url: "https://canvas.example/chat/my-flow",
    });

    expect(shareUrl).toBe("https://canvas.example/chat/my-flow");
  });

  it("extracts cron config from graph index", () => {
    const config = extractCronConfigFromVersionGraph({
      index: {
        cron: [
          {
            expression: "0 9 * * MON-FRI",
            timezone: "UTC",
            allow_overlapping: false,
          },
        ],
      },
    });

    expect(config).toEqual({
      expression: "0 9 * * MON-FRI",
      timezone: "UTC",
      allow_overlapping: false,
      start_at: undefined,
      end_at: undefined,
    });
  });

  it("extracts cron config from graph nodes", () => {
    const config = extractCronConfigFromVersionGraph({
      nodes: [
        {
          type: "CronTriggerNode",
          expression: "*/15 * * * *",
          timezone: "Europe/Amsterdam",
          allow_overlapping: true,
        },
      ],
    });

    expect(config).toEqual({
      expression: "*/15 * * * *",
      timezone: "Europe/Amsterdam",
      allow_overlapping: true,
      start_at: undefined,
      end_at: undefined,
    });
  });

  it("extracts cron config from script graph summary nodes", () => {
    const config = extractCronConfigFromVersionGraph({
      format: "langgraph-script",
      summary: {
        nodes: [
          {
            type: "CronTriggerNode",
            expression: "0 12 * * *",
            timezone: "UTC",
            allow_overlapping: false,
          },
        ],
      },
    });

    expect(config).toEqual({
      expression: "0 12 * * *",
      timezone: "UTC",
      allow_overlapping: false,
      start_at: undefined,
      end_at: undefined,
    });
  });

  it("throws when multiple cron triggers exist", () => {
    expect(() =>
      extractCronConfigFromVersionGraph({
        nodes: [
          { type: "CronTriggerNode", expression: "0 * * * *" },
          { type: "CronTriggerNode", expression: "30 * * * *" },
        ],
      }),
    ).toThrow("Workflow contains multiple cron triggers.");
  });

  it("normalizes the Telegram heartbeat template to allow overlapping when scheduling", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse([
        {
          id: "v1",
          workflow_id: "wf-heartbeat",
          version: 1,
          graph: {
            index: {
              cron: [
                {
                  expression: "* * * * *",
                  timezone: "UTC",
                  allow_overlapping: false,
                },
              ],
            },
          },
          metadata: {
            template_id: "template-telegram-heartbeat",
          },
          runnable_config: null,
          notes: null,
          created_by: "canvas",
          created_at: "2026-03-10T10:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
        },
      ]),
      jsonResponse({
        expression: "* * * * *",
        timezone: "UTC",
        allow_overlapping: true,
      }),
    ]);

    const result = await scheduleWorkflowFromLatestVersion("wf-heartbeat");

    expect(result.status).toBe("scheduled");
    expect(result.config?.allow_overlapping).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(2);

    const requestBody = JSON.parse(
      String(mockFetch.mock.calls[1]?.[1]?.body ?? "{}"),
    ) as { allow_overlapping?: boolean };
    expect(requestBody.allow_overlapping).toBe(true);
  });

  it("triggers a workflow run using the latest version", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse([
        {
          id: "v1",
          workflow_id: "wf-1",
          version: 1,
          graph: { format: "langgraph-script", source: "print('old')" },
          metadata: {},
          runnable_config: null,
          notes: null,
          created_by: "canvas",
          created_at: "2026-03-10T09:00:00Z",
          updated_at: "2026-03-10T09:00:00Z",
        },
        {
          id: "v2",
          workflow_id: "wf-1",
          version: 2,
          graph: { format: "langgraph-script", source: "print('new')" },
          metadata: {},
          runnable_config: null,
          notes: null,
          created_by: "canvas",
          created_at: "2026-03-10T10:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
        },
      ]),
      jsonResponse(
        {
          id: "run-1",
          workflow_id: "wf-1",
          workflow_version_id: "v2",
          status: "pending",
          triggered_by: "canvas",
          input_payload: {},
        },
        201,
      ),
    ]);

    const payload = await triggerWorkflowRun("wf-1");

    expect(payload.id).toBe("run-1");
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(String(mockFetch.mock.calls[1]?.[0])).toContain(
      "/api/workflows/wf-1/runs",
    );

    const requestBody = JSON.parse(
      String(mockFetch.mock.calls[1]?.[1]?.body ?? "{}"),
    ) as { workflow_version_id?: string; triggered_by?: string };
    expect(requestBody.workflow_version_id).toBe("v2");
    expect(requestBody.triggered_by).toBe("canvas");
  });

  it("fetches workflow remediations with filters", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse([
        {
          id: "rem-1",
          workflow_id: "wf-1",
          workflow_version_id: "v1",
          run_id: "run-1",
          status: "fixed",
          fingerprint: "fp",
          version_checksum: "checksum",
          attempt_count: 1,
          classification: "workflow_fixable",
          action: "create_workflow_version",
          developer_note: "Fixed workflow source.",
          created_version_id: "v2",
        },
      ]),
    ]);

    const remediations = await fetchWorkflowRemediations({
      workflowId: "wf-1",
      runId: "run-1",
      status: "fixed",
      limit: 25,
    });

    expect(remediations[0]?.id).toBe("rem-1");
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/workflow-remediations?workflow_id=wf-1&run_id=run-1&status=fixed&limit=25",
    );
  });

  it("selects the highest workflow version even when the API response is unsorted", () => {
    const latest = selectLatestWorkflowVersion([
      {
        id: "v2",
        workflow_id: "wf-1",
        version: 2,
        graph: { format: "langgraph-script", source: "print('mid')" },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "canvas",
        created_at: "2026-03-10T10:00:00Z",
        updated_at: "2026-03-10T10:00:00Z",
      },
      {
        id: "v1",
        workflow_id: "wf-1",
        version: 1,
        graph: { format: "langgraph-script", source: "print('old')" },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "canvas",
        created_at: "2026-03-10T09:00:00Z",
        updated_at: "2026-03-10T09:00:00Z",
      },
      {
        id: "v3",
        workflow_id: "wf-1",
        version: 3,
        graph: { format: "langgraph-script", source: "print('new')" },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "canvas",
        created_at: "2026-03-10T11:00:00Z",
        updated_at: "2026-03-10T11:00:00Z",
      },
    ]);

    expect(latest?.id).toBe("v3");
  });

  it("fetches compact workflow canvas data", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse({
        workflow: {
          id: "wf-1",
          handle: "wf-1",
          name: "Canvas Flow",
          slug: "canvas-flow",
          description: "Test",
          tags: ["draft"],
          is_archived: false,
          is_public: false,
          require_login: false,
          published_at: null,
          published_by: null,
          created_at: "2026-03-10T09:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
          share_url: null,
        },
        versions: [
          {
            id: "v1",
            workflow_id: "wf-1",
            version: 1,
            mermaid: "graph TD; A-->B",
            has_cron_trigger: true,
            metadata: {},
            runnable_config: null,
            notes: "First version",
            created_by: "canvas",
            created_at: "2026-03-10T09:00:00Z",
            updated_at: "2026-03-10T09:00:00Z",
          },
        ],
      }),
    ]);

    const payload = await fetchWorkflowCanvasData("wf-1");

    expect(payload?.workflow.id).toBe("wf-1");
    expect(payload?.versions).toHaveLength(1);
    expect(payload?.versions[0]?.mermaid).toBe("graph TD; A-->B");
    expect(payload?.versions[0]?.has_cron_trigger).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/workflows/wf-1/canvas",
    );
  });

  it("fetches workflow credential readiness", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse({
        workflow_id: "wf-1",
        status: "missing",
        referenced_credentials: [
          {
            name: "openai_api_key",
            placeholders: ["[[openai_api_key]]"],
            available: true,
            credential_id: "cred-1",
            provider: "openai",
          },
        ],
        available_credentials: ["openai_api_key"],
        missing_credentials: ["telegram_chat_id"],
      }),
    ]);

    const payload = await fetchWorkflowCredentialReadiness("wf-1");

    expect(payload?.status).toBe("missing");
    expect(payload?.missing_credentials).toEqual(["telegram_chat_id"]);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/workflows/wf-1/credentials/readiness",
    );
  });

  it("fetches backend plugin availability", async () => {
    queueResponses([
      jsonResponse({
        plugins: [
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
    ]);

    const payload = await fetchSystemPlugins();

    expect(payload.plugins).toHaveLength(1);
    expect(payload.plugins[0]?.name).toBe("orcheo-plugin-lark-listener");
    expect(payload.plugins[0]?.loaded).toBe(true);
  });

  it("fetches workflow listener health and metrics", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse([
        {
          subscription_id: "sub-1",
          node_name: "telegram_listener",
          platform: "telegram",
          status: "active",
          bot_identity_key: "telegram:primary",
          runtime_status: "healthy",
          consecutive_failures: 0,
        },
      ]),
      jsonResponse({
        workflow_id: "wf-1",
        total_subscriptions: 1,
        active_subscriptions: 1,
        blocked_subscriptions: 0,
        paused_subscriptions: 0,
        disabled_subscriptions: 0,
        error_subscriptions: 0,
        healthy_runtimes: 1,
        reconnecting_runtimes: 0,
        stalled_listeners: 0,
        dispatch_failures: 0,
        by_platform: [
          {
            platform: "telegram",
            total: 1,
            healthy: 1,
            paused: 0,
            errors: 0,
          },
        ],
        alerts: [],
      }),
    ]);

    const listeners = await fetchWorkflowListeners("wf-1");
    const metrics = await fetchWorkflowListenerMetrics("wf-1");

    expect(listeners).toHaveLength(1);
    expect(listeners[0]?.runtime_status).toBe("healthy");
    expect(metrics?.healthy_runtimes).toBe(1);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("pauses and resumes workflow listeners", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse({
        subscription_id: "sub-1",
        node_name: "telegram_listener",
        platform: "telegram",
        status: "paused",
        bot_identity_key: "telegram:primary",
        runtime_status: "healthy",
        consecutive_failures: 0,
      }),
      jsonResponse({
        subscription_id: "sub-1",
        node_name: "telegram_listener",
        platform: "telegram",
        status: "active",
        bot_identity_key: "telegram:primary",
        runtime_status: "healthy",
        consecutive_failures: 0,
      }),
    ]);

    const paused = await pauseWorkflowListener("wf-1", "sub-1");
    const resumed = await resumeWorkflowListener("wf-1", "sub-1", "tester");

    expect(paused.status).toBe("paused");
    expect(resumed.status).toBe("active");
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/workflows/wf-1/listeners/sub-1/pause",
    );
    expect(String(mockFetch.mock.calls[1]?.[0])).toContain(
      "/api/workflows/wf-1/listeners/sub-1/resume",
    );
  });
});
