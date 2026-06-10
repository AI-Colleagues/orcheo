import { describe, expect, it } from "vitest";

import {
  applyTraceResponse,
  buildTraceViewerData,
  createEmptyTraceEntry,
  deriveThreadStitchedViewerDataList,
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
        "orcheo.workflow.state.before": { count: 1 },
        "orcheo.workflow.state.after": { count: 2, result: "ok" },
        "orcheo.workflow.state.redacted": true,
        "orcheo.workflow.state.truncated": true,
      },
      events: [],
      status: { code: "OK" },
    },
  ],
  page_info: { has_next_page: false, cursor: null },
};

describe("trace helpers", () => {
  it("extracts workflow state metadata from span attributes", () => {
    const entry = applyTraceResponse(
      createEmptyTraceEntry("exec-1"),
      traceResponse,
    );
    const metadata = entry.spanMetadata["span-1"];

    expect(metadata?.workflowStateBefore).toEqual({ count: 1 });
    expect(metadata?.workflowStateAfter).toEqual({ count: 2, result: "ok" });
    expect(metadata?.workflowStateRedacted).toBe(true);
    expect(metadata?.workflowStateTruncated).toBe(true);
  });

  it("attaches workflow state metadata to rendered trace spans", () => {
    const entry = applyTraceResponse(
      createEmptyTraceEntry("exec-1"),
      traceResponse,
    );
    const viewer = buildTraceViewerData(entry);
    const spanMetadata = viewer?.spans[0]?.metadata as
      | {
          workflowStateBefore?: unknown;
          workflowStateAfter?: unknown;
        }
      | undefined;

    expect(spanMetadata?.workflowStateBefore).toEqual({ count: 1 });
    expect(spanMetadata?.workflowStateAfter).toEqual({
      count: 2,
      result: "ok",
    });
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
