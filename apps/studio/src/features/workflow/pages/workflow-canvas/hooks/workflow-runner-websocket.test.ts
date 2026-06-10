import { describe, expect, it, vi } from "vitest";

import { setupExecutionWebSocket } from "./workflow-runner-websocket";

describe("setupExecutionWebSocket", () => {
  it("includes stored runnable config in the run payload", () => {
    const send = vi.fn();
    const ws = {
      send,
      close: vi.fn(),
      onopen: null,
      onmessage: null,
      onerror: null,
      onclose: null,
    } as unknown as WebSocket;

    setupExecutionWebSocket({
      ws,
      executionId: "run-1",
      config: { format: "langgraph-script", source: "print('hi')" },
      graphToCanvas: { send_telegram_hello: "send_telegram_hello" },
      storedRunnableConfig: { tags: ["canvas"], run_name: "telegram" },
      nodes: [],
      currentWorkflowId: "wf-1",
      isMountedRef: { current: true },
      applyExecutionUpdate: vi.fn(),
      setIsRunning: vi.fn(),
      setExecutions: vi.fn(),
      websocketRef: { current: ws },
      onTraceUpdate: vi.fn(),
    });

    ws.onopen?.(new Event("open"));

    expect(send).toHaveBeenCalledTimes(1);
    expect(JSON.parse(send.mock.calls[0]?.[0] as string)).toMatchObject({
      type: "run_workflow",
      execution_id: "run-1",
      stored_runnable_config: {
        tags: ["canvas"],
        run_name: "telegram",
      },
    });
  });
});
