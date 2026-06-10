import type { TraceSpan } from "@evilmartians/agent-prism-types";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { DetailsViewInputOutputTab } from "./DetailsViewInputOutputTab";

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

  it("renders a workflow-state diff and supports toggling full snapshots", async () => {
    const user = userEvent.setup();
    const span = createSpan({
      metadata: {
        workflowStateBefore: { count: 1, inputs: { question: "hello" } },
        workflowStateAfter: {
          count: 2,
          inputs: { question: "hello" },
          result: "done",
        },
      },
    });

    render(<DetailsViewInputOutputTab data={span} />);

    expect(screen.getByText(/state diff/i)).toBeInTheDocument();
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

  it("shows snapshot redaction and truncation notices", () => {
    const span = createSpan({
      metadata: {
        workflowStateBefore: { api_key: "[REDACTED]" },
        workflowStateAfter: { api_key: "[REDACTED]" },
        workflowStateRedacted: true,
        workflowStateTruncated: true,
      },
    });

    render(<DetailsViewInputOutputTab data={span} />);

    expect(
      screen.getByText(/sensitive fields were redacted/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/large values were truncated/i),
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
