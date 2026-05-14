import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowGalleryTabs } from "./workflow-gallery-tabs";

vi.mock("./workflow-card", () => ({
  WorkflowCard: () => <div data-testid="workflow-card" />,
}));

afterEach(() => {
  cleanup();
});

describe("WorkflowGalleryTabs", () => {
  it("shows a loading screen while workflows are being fetched", () => {
    render(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading
        sortedWorkflows={[]}
        tabCounts={{ all: 0, pinned: 0, templates: 0 }}
        isTemplateView={false}
        workspaceLabel="AI Company"
        searchQuery=""
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(screen.getByText(/loading colleagues/i)).toBeTruthy();
    expect(screen.queryByText(/import starter pack/i)).toBeNull();
  });

  it("keeps templates visible while workspace workflows are still loading", () => {
    render(
      <WorkflowGalleryTabs
        selectedTab="templates"
        onSelectedTabChange={vi.fn()}
        isLoading
        sortedWorkflows={[
          {
            id: "template-1",
            name: "Starter",
            description: "Template",
            createdAt: "2026-01-01T00:00:00.000Z",
            updatedAt: "2026-01-01T00:00:00.000Z",
            owner: { id: "owner-1", name: "Owner", avatar: "" },
            nodes: [],
            edges: [],
          },
        ]}
        tabCounts={{ all: 1, pinned: 0, templates: 1 }}
        isTemplateView
        workspaceLabel="AI Company"
        searchQuery=""
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(screen.queryByText(/loading colleagues/i)).toBeNull();
    expect(screen.getByTestId("workflow-card")).toBeTruthy();
  });

  it("renders workflow counts in each gallery tab", () => {
    render(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[]}
        tabCounts={{ all: 12, pinned: 3, templates: 20 }}
        isTemplateView={false}
        workspaceLabel="AI Company"
        searchQuery=""
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("tab", { name: /ai colleagues 12/i }),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: /starred 3/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /candidates 20/i })).toBeTruthy();
  });
});
