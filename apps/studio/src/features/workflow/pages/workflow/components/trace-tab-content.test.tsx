import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TraceViewerData } from "@features/workflow/components/trace/agent-prism";
import type { TraceTabContentProps } from "./trace-tab-content";

import { TraceTabContent } from "./trace-tab-content";

vi.mock(
  "@features/workflow/components/trace/agent-prism/theme/theme.css",
  () => ({}),
  { virtual: true },
);

const traceViewerMock = vi.hoisted(() => vi.fn());

vi.mock("@features/workflow/components/trace/agent-prism", () => ({
  TraceViewer: traceViewerMock,
}));

const sampleViewerData: TraceViewerData = {
  traceRecord: {
    id: "exec-1",
    name: "Trace 1",
    spansCount: 3,
    durationMs: 1200,
    agentDescription: "success",
    totalTokens: 42,
    startTime: Date.now(),
  },
  spans: [],
  threadId: "thread-1",
};

const sampleViewerData2: TraceViewerData = {
  traceRecord: {
    id: "exec-2",
    name: "Trace 2",
    spansCount: 2,
    durationMs: 900,
    agentDescription: "success",
    totalTokens: 24,
    startTime: Date.now() + 1000,
  },
  spans: [],
  threadId: "thread-1",
};

const createProps = (
  overrides: Partial<TraceTabContentProps> = {},
): TraceTabContentProps => ({
  error: undefined,
  viewerData: [sampleViewerData],
  activeViewer: sampleViewerData,
  onRefresh: vi.fn(),
  isRefreshing: false,
  onSelectTrace: vi.fn(),
  ...overrides,
});

describe("TraceTabContent", () => {
  beforeEach(() => {
    traceViewerMock.mockImplementation(
      ({
        data,
        activeTraceId,
      }: {
        data: TraceViewerData[];
        activeTraceId?: string;
      }) => (
        <div data-testid="trace-viewer">
          <span data-testid="trace-viewer-count">
            {Array.isArray(data) ? data.length : 0}
          </span>
          <span data-testid="trace-viewer-active-id">
            {activeTraceId ?? "none"}
          </span>
        </div>
      ),
    );
    vi.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    traceViewerMock.mockReset();
  });

  it("shows empty state when there is no data", () => {
    render(
      <TraceTabContent
        {...createProps({
          viewerData: [],
          activeViewer: undefined,
        })}
      />,
    );

    expect(screen.getByTestId("trace-empty-state")).toBeInTheDocument();
    expect(screen.queryByTestId("trace-viewer")).not.toBeInTheDocument();
  });

  it("renders error alert when trace fails to load", () => {
    render(
      <TraceTabContent
        {...createProps({
          error: "Server unavailable",
          viewerData: [],
          activeViewer: undefined,
        })}
      />,
    );

    expect(screen.getByText(/unable to load trace/i)).toBeInTheDocument();
    expect(screen.getByText(/server unavailable/i)).toBeInTheDocument();
  });

  it("renders viewer when data is available", () => {
    render(<TraceTabContent {...createProps()} />);

    expect(screen.getByTestId("trace-viewer")).toBeInTheDocument();
    expect(screen.getByTestId("trace-viewer-count")).toHaveTextContent("1");
    expect(
      screen.queryByText(/recorded nodes and events/i),
    ).not.toBeInTheDocument();
  });

  it("wraps the viewer in a full-width container", () => {
    render(<TraceTabContent {...createProps()} />);

    expect(screen.getByTestId("trace-viewer").parentElement).toHaveClass(
      "w-full",
      "min-w-0",
    );
  });

  it("passes the active trace id to the viewer", () => {
    render(<TraceTabContent {...createProps()} />);

    expect(screen.getByTestId("trace-viewer-active-id")).toHaveTextContent(
      sampleViewerData.traceRecord.id,
    );
  });

  it("calls refresh handler when refresh button is clicked", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    render(
      <TraceTabContent
        {...createProps({
          onRefresh,
        })}
      />,
    );

    const [refreshButton] = screen.getAllByRole("button", { name: /refresh/i });
    await user.click(refreshButton);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("invokes onSelectTrace when the viewer requests a trace change", () => {
    const onSelectTrace = vi.fn();
    traceViewerMock.mockImplementation(
      ({
        onTraceSelect,
      }: {
        onTraceSelect?: (trace: TraceViewerData["traceRecord"]) => void;
      }) => (
        <div data-testid="trace-viewer">
          <button
            type="button"
            onClick={() => {
              onTraceSelect?.(sampleViewerData.traceRecord);
            }}
          >
            select-trace
          </button>
        </div>
      ),
    );

    const { getByRole } = render(
      <TraceTabContent
        {...createProps({
          onSelectTrace,
        })}
      />,
    );

    getByRole("button", { name: /select-trace/i }).click();

    expect(onSelectTrace).toHaveBeenCalledWith(sampleViewerData.traceRecord.id);
  });

  it("toggles stitched mode and passes grouped traces to the viewer", async () => {
    const user = userEvent.setup();

    render(
      <TraceTabContent
        {...createProps({
          viewerData: [sampleViewerData, sampleViewerData2],
          activeViewer: sampleViewerData2,
        })}
      />,
    );

    expect(screen.getByTestId("trace-viewer-count")).toHaveTextContent("2");
    expect(screen.getByTestId("trace-viewer-active-id")).toHaveTextContent(
      "exec-2",
    );

    await user.click(screen.getByRole("button", { name: /stitched: off/i }));

    expect(screen.getByRole("button", { name: /stitched: on/i }));
    expect(screen.getByTestId("trace-viewer-count")).toHaveTextContent("1");
    expect(screen.getByTestId("trace-viewer-active-id")).toHaveTextContent(
      "exec-2",
    );
  });
});
