import type { TraceSpan } from "@evilmartians/agent-prism-types";

import type { ReactElement } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TraceSpanStateResponse } from "@features/workflow/pages/workflow/helpers/trace";

import { DetailsViewInputOutputTab } from "./DetailsViewInputOutputTab";
import {
  SpanStateLoaderProvider,
  type SpanStateLoader,
} from "./SpanStateContext";

const nodeMetadata = { executionId: "exec-1", hasWorkflowState: true };

const renderWithLoader = (ui: ReactElement, loader: SpanStateLoader) =>
  render(
    <SpanStateLoaderProvider value={loader}>{ui}</SpanStateLoaderProvider>,
  );

const stateResponse = (
  overrides: Partial<TraceSpanStateResponse> = {},
): TraceSpanStateResponse => ({
  span_id: "span-1",
  before: {},
  after: {},
  redacted: false,
  truncated: false,
  ...overrides,
});

const createSpan = (overrides: Partial<TraceSpan> = {}): TraceSpan =>
  ({
    id: "span-1",
    title: "Node 1",
    startTime: new Date("2024-01-01T00:00:00Z"),
    endTime: new Date("2024-01-01T00:00:01Z"),
    duration: 1000,
    type: "llm_call",
    raw: "{}",
    status: "success",
    ...overrides,
  }) as TraceSpan;

describe("DetailsViewInputOutputTab", () => {
  afterEach(() => {
    cleanup();
  });

  it("loads the workflow-state diff and supports toggling full snapshots", async () => {
    const user = userEvent.setup();
    const loader = vi.fn<SpanStateLoader>().mockResolvedValue(
      stateResponse({
        before: { count: 1, inputs: { question: "hello" } },
        after: {
          count: 2,
          inputs: { question: "hello" },
          result: "done",
        },
      }),
    );
    const span = createSpan({ metadata: nodeMetadata });

    renderWithLoader(<DetailsViewInputOutputTab data={span} />, loader);

    expect(screen.getByTestId("span-state-loading")).toBeInTheDocument();
    expect(await screen.findByText(/state diff/i)).toBeInTheDocument();
    expect(loader).toHaveBeenCalledWith("exec-1", "span-1");
    expect(screen.getByText("count")).toBeInTheDocument();
    expect(screen.getByText("result")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /show full snapshots/i }),
    );

    expect(
      screen.getByRole("button", { name: /hide full snapshots/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
  });

  it("shows snapshot redaction and truncation notices", async () => {
    const loader = vi.fn<SpanStateLoader>().mockResolvedValue(
      stateResponse({
        before: { api_key: "[REDACTED]" },
        after: { api_key: "[REDACTED]" },
        redacted: true,
        truncated: true,
      }),
    );
    const span = createSpan({ metadata: nodeMetadata });

    renderWithLoader(<DetailsViewInputOutputTab data={span} />, loader);

    expect(
      await screen.findByText(/sensitive fields were redacted/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/large values were truncated/i),
    ).toBeInTheDocument();
  });

  it("reports a failed workflow-state request", async () => {
    const loader = vi
      .fn<SpanStateLoader>()
      .mockRejectedValue(new Error("boom"));
    const span = createSpan({ metadata: nodeMetadata });

    renderWithLoader(<DetailsViewInputOutputTab data={span} />, loader);

    expect(
      await screen.findByText(/couldn't load the workflow state.*boom/i),
    ).toBeInTheDocument();
  });

  it("does not request state for spans that are not nodes", () => {
    const loader = vi.fn<SpanStateLoader>();
    const span = createSpan({ metadata: { executionId: "exec-1" } });

    renderWithLoader(<DetailsViewInputOutputTab data={span} />, loader);

    expect(loader).not.toHaveBeenCalled();
    expect(
      screen.getByText(/no input or output data available for this span/i),
    ).toBeInTheDocument();
  });

  it("falls back to legacy input/output rendering when no state snapshots exist", () => {
    const span = createSpan({});

    render(<DetailsViewInputOutputTab data={span} />);

    expect(
      screen.getByText(/no input or output data available for this span/i),
    ).toBeInTheDocument();
  });
});
