import { describe, expect, it } from "vitest";

import {
  applyTraceResponse,
  buildTraceViewerData,
  createEmptyTraceEntry,
  deriveThreadStitchedViewerDataList,
  deriveViewerDataList,
  markTraceLoading,
  type TraceResponse,
} from "./trace";

const traceResponse: TraceResponse = {
  execution: {
    id: "exec-1",
    status: "completed",
    trace_id: "trace-1",
    token_usage: { input: 1, output: 1 },
  },
  spans: [
    {
      span_id: "span-1",
      name: "node-1",
      attributes: {
        "orcheo.node.id": "node-1",
      },
      events: [],
      status: { code: "OK" },
    },
  ],
  page_info: { has_next_page: false, cursor: null },
};

describe("trace helpers", () => {
  it("marks node spans as able to load workflow state on demand", () => {
    const entry = applyTraceResponse(createEmptyTraceEntry("exec-1"), {
      ...traceResponse,
      spans: [
        ...traceResponse.spans,
        {
          span_id: "root",
          name: "workflow.execution",
          attributes: {},
          status: { code: "OK" },
        },
      ],
    });

    expect(entry.spanMetadata["span-1"]).toMatchObject({
      executionId: "exec-1",
      nodeId: "node-1",
      hasWorkflowState: true,
    });
    expect(entry.spanMetadata["root"]?.hasWorkflowState).toBeUndefined();
  });

  it("attaches execution id metadata to rendered trace spans", () => {
    const entry = applyTraceResponse(
      createEmptyTraceEntry("exec-1"),
      traceResponse,
    );
    const viewer = buildTraceViewerData(entry);

    expect(viewer?.loadStatus).toBe("ready");
    expect(viewer?.spans[0]?.metadata).toMatchObject({
      executionId: "exec-1",
      hasWorkflowState: true,
    });
  });

  it("lists executions whose traces are not loaded as placeholders", () => {
    const loaded = applyTraceResponse(createEmptyTraceEntry("exec-1"), {
      ...traceResponse,
      execution: {
        ...traceResponse.execution,
        started_at: "2024-01-01T12:00:00Z",
      },
    });
    const loading = markTraceLoading(createEmptyTraceEntry("exec-2"));

    const viewerData = deriveViewerDataList(
      { "exec-1": loaded, "exec-2": loading },
      {},
      [
        { id: "exec-1", status: "success", startTime: "2024-01-01T12:00:00Z" },
        {
          id: "exec-2",
          status: "running",
          startTime: "2024-01-01T12:05:00Z",
        },
        {
          id: "exec-3",
          status: "failed",
          startTime: "2024-01-01T11:00:00Z",
          endTime: "2024-01-01T11:00:02Z",
        },
      ],
    );

    expect(viewerData.map((item) => item.traceRecord.id)).toEqual([
      "exec-2",
      "exec-1",
      "exec-3",
    ]);
    expect(viewerData.map((item) => item.loadStatus)).toEqual([
      "loading",
      "ready",
      "idle",
    ]);
    const placeholder = viewerData[2];
    expect(placeholder?.spans).toEqual([]);
    expect(placeholder?.traceRecord.durationMs).toBe(2000);
    expect(placeholder?.badges).toEqual([{ label: "Status: failed" }]);
  });

  it("keeps a loading execution missing from the summaries listed", () => {
    const viewerData = deriveViewerDataList({
      "exec-new": markTraceLoading(createEmptyTraceEntry("exec-new")),
      "exec-idle": createEmptyTraceEntry("exec-idle"),
    });

    expect(viewerData.map((item) => item.traceRecord.id)).toEqual(["exec-new"]);
    expect(viewerData[0]?.loadStatus).toBe("loading");
  });

  it("exposes thread id on viewer data when metadata includes thread_id", () => {
    const entry = applyTraceResponse(createEmptyTraceEntry("exec-1"), {
      ...traceResponse,
      execution: {
        ...traceResponse.execution,
        thread_id: "thread-1",
      },
    });
    const viewer = buildTraceViewerData(entry);

    expect(viewer?.threadId).toBe("thread-1");
  });

  it("stitches traces that share the same thread id into one grouped trace", () => {
    const firstEntry = applyTraceResponse(createEmptyTraceEntry("exec-1"), {
      ...traceResponse,
      execution: {
        ...traceResponse.execution,
        id: "exec-1",
        thread_id: "thread-shared",
        started_at: "2024-01-01T12:00:00Z",
        finished_at: "2024-01-01T12:00:02Z",
      },
    });
    const secondEntry = applyTraceResponse(createEmptyTraceEntry("exec-2"), {
      ...traceResponse,
      execution: {
        ...traceResponse.execution,
        id: "exec-2",
        trace_id: "trace-2",
        thread_id: "thread-shared",
        started_at: "2024-01-01T12:00:03Z",
        finished_at: "2024-01-01T12:00:05Z",
      },
      spans: [
        {
          ...traceResponse.spans[0],
          span_id: "span-2",
        },
      ],
    });

    const firstViewer = buildTraceViewerData(firstEntry);
    const secondViewer = buildTraceViewerData(secondEntry);

    if (!firstViewer || !secondViewer) {
      throw new Error("expected both viewer payloads to be built");
    }

    const stitched = deriveThreadStitchedViewerDataList(
      [firstViewer, secondViewer],
      "exec-2",
    );

    expect(stitched).toHaveLength(1);
    expect(stitched[0].threadId).toBe("thread-shared");
    expect(stitched[0].traceRecord.id).toBe("exec-2");
    expect(stitched[0].traceRecord.agentDescription).toContain("2 executions");
    expect(stitched[0].spans).toHaveLength(2);
    expect(stitched[0].spans[0].title).toContain("Execution");
  });
});
