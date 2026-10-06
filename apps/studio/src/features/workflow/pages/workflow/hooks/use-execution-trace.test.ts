import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/auth-fetch", () => ({ authFetch: authFetchMock }));
vi.mock("@/hooks/use-toast", () => ({ toast: vi.fn() }));

import { useExecutionTrace } from "./use-execution-trace";

const BASE_URL = "http://backend.test";

const jsonResponse = (payload: unknown, status = 200) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }) as Response;

const traceResponse = (executionId: string) => ({
  execution: {
    id: executionId,
    status: "completed",
    trace_id: `trace-${executionId}`,
    started_at: "2024-01-01T12:00:00Z",
    finished_at: "2024-01-01T12:00:01Z",
  },
  spans: [
    {
      span_id: `${executionId}-node`,
      name: "node",
      attributes: { "orcheo.node.id": "node" },
      status: { code: "OK" },
    },
  ],
  page_info: { has_next_page: false, cursor: null },
});

const executions = [
  { id: "exec-1", status: "success", startTime: "2024-01-01T12:00:00Z" },
  { id: "exec-2", status: "success", startTime: "2024-01-01T11:00:00Z" },
  { id: "exec-3", status: "failed", startTime: "2024-01-01T10:00:00Z" },
];

const traceRequests = () =>
  authFetchMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => /\/trace(\?|$)/.test(url));

const renderTraceHook = (activeExecutionId: string | null = "exec-1") => {
  const isMountedRef = { current: true };
  return renderHook(
    (props: { activeExecutionId: string | null }) =>
      useExecutionTrace({
        backendBaseUrl: BASE_URL,
        workflowId: "wf-1",
        activeExecutionId: props.activeExecutionId,
        isMountedRef,
        executions,
      }),
    { initialProps: { activeExecutionId } },
  );
};

describe("useExecutionTrace", () => {
  beforeEach(() => {
    authFetchMock.mockImplementation(async (url: string) => {
      const match = /\/api\/executions\/([^/]+)\/trace/.exec(url);
      if (url.includes("/spans/")) {
        return jsonResponse({
          span_id: "exec-1-node",
          before: { count: 1 },
          after: { count: 2 },
          redacted: false,
          truncated: false,
        });
      }
      if (match) {
        return jsonResponse(traceResponse(match[1]!));
      }
      return jsonResponse([]);
    });
  });

  afterEach(() => {
    authFetchMock.mockReset();
  });

  it("fetches only the active trace and lists the rest as placeholders", async () => {
    const { result } = renderTraceHook();

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(traceRequests()).toHaveLength(1);
    expect(traceRequests()[0]).toContain("/api/executions/exec-1/trace");
    expect(
      result.current.viewerData.map((item) => [
        item.traceRecord.id,
        item.loadStatus,
      ]),
    ).toEqual([
      ["exec-1", "ready"],
      ["exec-2", "idle"],
      ["exec-3", "idle"],
    ]);
  });

  it("fetches another trace once it is selected", async () => {
    const { result, rerender } = renderTraceHook();
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ activeExecutionId: "exec-2" });

    await waitFor(() =>
      expect(result.current.activeTraceViewer?.traceRecord.id).toBe("exec-2"),
    );
    expect(traceRequests()).toHaveLength(2);
  });

  it("loads every listed trace on demand", async () => {
    const { result } = renderTraceHook();
    await waitFor(() => expect(result.current.status).toBe("ready"));

    await act(async () => {
      await result.current.loadAll();
    });

    expect(traceRequests()).toHaveLength(3);
    expect(
      result.current.viewerData.every((item) => item.loadStatus === "ready"),
    ).toBe(true);
  });

  it("does not retry a failed trace fetch in a loop", async () => {
    authFetchMock.mockImplementation(async () =>
      jsonResponse({ detail: "boom" }, 500),
    );
    const { result } = renderTraceHook();

    await waitFor(() => expect(result.current.status).toBe("error"), {
      timeout: 3000,
    });
    const attempts = traceRequests().length;
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(traceRequests()).toHaveLength(attempts);
  });

  it("retries a failed trace when it is selected again", async () => {
    authFetchMock.mockImplementation(async () =>
      jsonResponse({ detail: "boom" }, 500),
    );
    const { result, rerender } = renderTraceHook();
    await waitFor(() => expect(result.current.status).toBe("error"), {
      timeout: 3000,
    });
    const attempts = traceRequests().length;

    rerender({ activeExecutionId: "exec-2" });
    await waitFor(() => expect(result.current.status).toBe("error"), {
      timeout: 3000,
    });
    rerender({ activeExecutionId: "exec-1" });

    await waitFor(() =>
      expect(
        traceRequests().filter((url) => url.includes("exec-1")).length,
      ).toBeGreaterThan(attempts),
    );
  });

  it("retries a failed trace after visiting an already-loaded one", async () => {
    let failExec1 = true;
    authFetchMock.mockImplementation(async (url: string) => {
      const match = /\/api\/executions\/([^/]+)\/trace/.exec(url);
      if (match && match[1] === "exec-1" && failExec1) {
        return jsonResponse({ detail: "boom" }, 500);
      }
      return match ? jsonResponse(traceResponse(match[1]!)) : jsonResponse([]);
    });
    const { result, rerender } = renderTraceHook("exec-2");
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ activeExecutionId: "exec-1" });
    await waitFor(() => expect(result.current.status).toBe("error"), {
      timeout: 3000,
    });

    rerender({ activeExecutionId: "exec-2" });
    expect(result.current.status).toBe("ready");

    failExec1 = false;
    rerender({ activeExecutionId: "exec-1" });

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.activeTraceViewer?.traceRecord.id).toBe("exec-1");
  });

  it("caches span workflow state per execution and span", async () => {
    const { result } = renderTraceHook(null);

    const first = await result.current.loadSpanState("exec-1", "exec-1-node");
    const second = await result.current.loadSpanState("exec-1", "exec-1-node");

    expect(first.after).toEqual({ count: 2 });
    expect(second).toBe(first);
    const stateCalls = authFetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/spans/exec-1-node/state"),
    );
    expect(stateCalls).toHaveLength(1);
  });

  it("retries span state after a failed request", async () => {
    authFetchMock.mockResolvedValueOnce(jsonResponse({ detail: "no" }, 500));
    const { result } = renderTraceHook(null);

    await expect(
      result.current.loadSpanState("exec-1", "exec-1-node"),
    ).rejects.toThrow(/^no \(500 Error\)\.$/);
    await expect(
      result.current.loadSpanState("exec-1", "exec-1-node"),
    ).resolves.toMatchObject({ span_id: "exec-1-node" });
  });
});
