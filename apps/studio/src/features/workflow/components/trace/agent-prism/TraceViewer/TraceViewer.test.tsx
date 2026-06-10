import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TraceRecord, TraceSpan } from "@evilmartians/agent-prism-types";

type LayoutMockProps = {
  selectedTraceId?: string;
  filteredSpans: TraceSpan[];
  handleTraceSelect: (trace: TraceRecord) => void;
  traceRecords: TraceRecord[];
};

const desktopLayoutMock = vi.fn((props: unknown) => {
  const { selectedTraceId, filteredSpans, handleTraceSelect, traceRecords } =
    props as LayoutMockProps;

  return (
    <div data-testid="desktop-layout">
      <span data-testid="selected-trace-id">{selectedTraceId ?? "none"}</span>
      <span data-testid="span-count">{filteredSpans.length}</span>
      <button
        type="button"
        onClick={() => handleTraceSelect(traceRecords.at(-1)!)}
      >
        select-last-trace
      </button>
    </div>
  );
});

vi.mock("../shared", () => ({
  useIsMobile: () => false,
}));

vi.mock("./TraceViewerDesktopLayout", () => ({
  TraceViewerDesktopLayout: (props: unknown) => desktopLayoutMock(props),
}));

vi.mock("./TraceViewerMobileLayout", () => ({
  TraceViewerMobileLayout: () => null,
}));

import { TraceViewer, type TraceViewerData } from "./TraceViewer";

const createSpan = (id: string): TraceSpan => ({
  id,
  title: `Span ${id}`,
  startTime: new Date("2024-01-01T00:00:00Z"),
  endTime: new Date("2024-01-01T00:00:01Z"),
  duration: 1000,
  type: "llm_call",
  raw: "{}",
  status: "success",
});

const createViewerData = (id: string, spanCount: number): TraceViewerData => {
  const spans = Array.from({ length: spanCount }, (_, index) =>
    createSpan(`${id}-span-${index}`),
  );

  return {
    traceRecord: {
      id,
      name: `Trace ${id}`,
      spansCount: spanCount,
      durationMs: spanCount * 100,
      agentDescription: "agent",
      totalTokens: spanCount * 10,
      startTime: Date.now(),
    },
    spans,
  };
};

describe("TraceViewer", () => {
  afterEach(() => {
    cleanup();
    desktopLayoutMock.mockClear();
  });

  it("focuses the active trace id and refreshes spans when data updates", () => {
    const initialData = [
      createViewerData("trace-1", 1),
      createViewerData("trace-2", 1),
    ];

    const { rerender } = render(
      <TraceViewer data={initialData} activeTraceId="trace-2" />,
    );

    const selectedTraceIds = () =>
      screen
        .getAllByTestId("selected-trace-id")
        .map((element) => element.textContent);
    const spanCounts = () =>
      screen.getAllByTestId("span-count").map((element) => element.textContent);

    expect(selectedTraceIds()).toContain("trace-2");
    expect(spanCounts()).toContain("1");

    const updatedTrace = {
      ...initialData[1],
      traceRecord: {
        ...initialData[1].traceRecord,
        spansCount: initialData[1].traceRecord.spansCount + 1,
      },
      spans: [...initialData[1].spans, createSpan("trace-2-span-1")],
    } satisfies TraceViewerData;

    rerender(
      <TraceViewer
        data={[initialData[0], updatedTrace]}
        activeTraceId="trace-2"
      />,
    );

    expect(selectedTraceIds()).toContain("trace-2");
    expect(spanCounts()).toContain("2");
  });

  it("preserves manual selections across data refreshes", async () => {
    const user = userEvent.setup();
    const initialData = [
      createViewerData("trace-1", 1),
      createViewerData("trace-2", 1),
    ];

    const { rerender } = render(<TraceViewer data={initialData} />);

    const [selectLastTraceButton] = screen.getAllByRole("button", {
      name: /select-last-trace/i,
    });

    await user.click(selectLastTraceButton);

    expect(
      screen
        .getAllByTestId("selected-trace-id")
        .map((element) => element.textContent),
    ).toContain("trace-2");

    const refreshedTrace = {
      ...initialData[1],
      traceRecord: {
        ...initialData[1].traceRecord,
        spansCount: initialData[1].traceRecord.spansCount + 1,
      },
      spans: [...initialData[1].spans, createSpan("trace-2-span-1")],
    } satisfies TraceViewerData;

    rerender(<TraceViewer data={[initialData[0], refreshedTrace]} />);

    expect(
      screen
        .getAllByTestId("selected-trace-id")
        .map((element) => element.textContent),
    ).toContain("trace-2");
    expect(
      screen.getAllByTestId("span-count").map((element) => element.textContent),
    ).toContain("2");
  });

  it("invokes onTraceSelect when a user picks a trace", () => {
    const initialData = [
      createViewerData("trace-1", 1),
      createViewerData("trace-2", 1),
    ];
    const handleTraceSelect = vi.fn();

    render(
      <TraceViewer data={initialData} onTraceSelect={handleTraceSelect} />,
    );

    const lastCall = desktopLayoutMock.mock.calls.at(
      -1,
    )?.[0] as LayoutMockProps;
    lastCall.handleTraceSelect(lastCall.traceRecords.at(-1)!);

    expect(handleTraceSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "trace-2" }),
    );
  });

  it("renders the desktop viewer inside a full-width wrapper", () => {
    render(<TraceViewer data={[createViewerData("trace-1", 1)]} />);

    expect(screen.getByTestId("desktop-layout").parentElement).toHaveClass(
      "w-full",
      "min-w-0",
    );
  });
});
