import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { TraceLoadStatus } from "./TraceViewer";

import { TraceViewerTreeViewContainer } from "./TraceViewerTreeViewContainer";

const renderContainer = (
  loadStatus: TraceLoadStatus | undefined,
  searchValue = "",
) =>
  render(
    <TraceViewerTreeViewContainer
      searchValue={searchValue}
      setSearchValue={vi.fn()}
      handleExpandAll={vi.fn()}
      handleCollapseAll={vi.fn()}
      filteredSpans={[]}
      selectedSpan={undefined}
      setSelectedSpan={vi.fn()}
      expandedSpansIds={[]}
      setExpandedSpansIds={vi.fn()}
      selectedTrace={{
        id: "exec-1",
        name: "exec-1",
        spansCount: 0,
        durationMs: 0,
        agentDescription: "running",
        loadStatus,
      }}
    />,
  );

describe("TraceViewerTreeViewContainer", () => {
  afterEach(() => {
    cleanup();
  });

  it.each<TraceLoadStatus>(["idle", "loading"])(
    "shows a skeleton while a %s trace has no spans",
    (status) => {
      renderContainer(status);

      expect(screen.getByTestId("trace-spans-loading")).toBeInTheDocument();
      expect(screen.queryByText(/0 spans/)).not.toBeInTheDocument();
      expect(
        screen.getByText(status === "loading" ? "Loading" : "exec-1"),
      ).toBeInTheDocument();
    },
  );

  it("explains a loaded trace without spans", () => {
    renderContainer("ready");

    expect(
      screen.getByText(/no spans recorded yet for this execution/i),
    ).toBeInTheDocument();
    expect(screen.getByText("0 spans")).toBeInTheDocument();
  });

  it("reports a trace that failed to load", () => {
    renderContainer("error");

    expect(screen.getByText(/couldn't load this trace/i)).toBeInTheDocument();
    expect(screen.getByText("Failed to load")).toBeInTheDocument();
  });

  it("reports empty search results rather than loading", () => {
    renderContainer("loading", "missing");

    expect(screen.getByText("No spans found")).toBeInTheDocument();
    expect(screen.queryByTestId("trace-spans-loading")).not.toBeInTheDocument();
  });
});
