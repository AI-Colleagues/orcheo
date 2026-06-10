import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TraceRecord } from "@evilmartians/agent-prism-types";

import { TraceList } from "./TraceList";

const createTrace = (index: number): TraceRecord => ({
  id: `trace-${index}`,
  name: `Trace ${index}`,
  spansCount: index,
  durationMs: 1000,
  agentDescription: `Description ${index}`,
  totalTokens: index,
  startTime: Date.now() + index,
});

describe("TraceList", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the first 20 traces and reveals more when load more is clicked", async () => {
    const user = userEvent.setup();
    const traces = Array.from({ length: 25 }, (_, index) =>
      createTrace(index + 1),
    );

    render(
      <TraceList
        traces={traces}
        expanded
        onExpandStateChange={vi.fn()}
        onTraceSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("Trace 1")).toBeInTheDocument();
    expect(screen.getByText("Trace 20")).toBeInTheDocument();
    expect(screen.queryByText("Trace 21")).not.toBeInTheDocument();

    const loadMoreButton = screen.getByRole("button", { name: /load more/i });
    await user.click(loadMoreButton);

    expect(screen.getByText("Trace 25")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
  });

  it("hides load more when there are 20 or fewer traces", () => {
    const traces = Array.from({ length: 20 }, (_, index) =>
      createTrace(index + 1),
    );

    render(
      <TraceList
        traces={traces}
        expanded
        onExpandStateChange={vi.fn()}
        onTraceSelect={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });

  it("resets back to 20 visible traces when the trace dataset changes", async () => {
    const user = userEvent.setup();
    const initialTraces = Array.from({ length: 25 }, (_, index) =>
      createTrace(index + 1),
    );

    const { rerender } = render(
      <TraceList
        traces={initialTraces}
        expanded
        onExpandStateChange={vi.fn()}
        onTraceSelect={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /load more/i }));
    expect(screen.getByText("Trace 25")).toBeInTheDocument();

    const refreshedTraces = Array.from({ length: 22 }, (_, index) =>
      createTrace(index + 101),
    );

    rerender(
      <TraceList
        traces={refreshedTraces}
        expanded
        onExpandStateChange={vi.fn()}
        onTraceSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("Trace 101")).toBeInTheDocument();
    expect(screen.getByText("Trace 120")).toBeInTheDocument();
    expect(screen.queryByText("Trace 121")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /load more/i }),
    ).toBeInTheDocument();
  });
});
