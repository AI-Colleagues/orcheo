import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowGalleryTabs } from "./workflow-gallery-tabs";

vi.mock("./workflow-card", () => ({
  WorkflowCard: () => <div data-testid="workflow-card" />,
}));

afterEach(() => {
  cleanup();
});

const renderTabs = (ui: ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

describe("WorkflowGalleryTabs", () => {
  it("shows a loading screen while workflows are being fetched", () => {
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading
        sortedWorkflows={[]}
        tabCounts={{ all: 0, pinned: 0, templates: 0 }}
        isTemplateView={false}
        workspaceLabel="AI Company"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
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
    renderTabs(
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
        onSearchQueryChange={vi.fn()}
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
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[]}
        tabCounts={{ all: 12, pinned: 3, templates: 20 }}
        isTemplateView={false}
        workspaceLabel="AI Company"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: /ai teams 12/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /starred 3/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /candidates 20/i })).toBeTruthy();
    expect(screen.getByPlaceholderText(/search colleagues/i)).toBeTruthy();
  });

  it("groups colleagues into collapsible team sections", async () => {
    const baseWorkflow = {
      name: "Colleague",
      description: "",
      createdAt: "2026-01-01T00:00:00.000Z",
      updatedAt: "2026-01-01T00:00:00.000Z",
      owner: { id: "owner-1", name: "Owner", avatar: "" },
      tags: [],
      nodes: [],
      edges: [],
    };
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[
          { ...baseWorkflow, id: "wf-1", teamId: "team-default" },
          { ...baseWorkflow, id: "wf-2", teamId: "team-sales" },
        ]}
        tabCounts={{ all: 2, pinned: 0, templates: 0 }}
        isTemplateView={false}
        teams={[
          { id: "team-default", slug: "acme", name: "Acme", is_default: true },
          { id: "team-sales", slug: "sales", name: "Sales", is_default: false },
        ]}
        workspaceLabel="Acme"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    const acmeHeader = screen.getByRole("button", { name: /Acme 1/i });
    const salesHeader = screen.getByRole("button", { name: /Sales 1/i });
    expect(acmeHeader).toBeTruthy();
    expect(salesHeader).toBeTruthy();
    expect(screen.getAllByTestId("workflow-card")).toHaveLength(2);

    // Collapsing a section hides its cards.
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    await user.click(acmeHeader);
    expect(screen.getAllByTestId("workflow-card")).toHaveLength(1);
  });

  it("groups candidates into sections mirroring the colleagues layout", async () => {
    const baseTemplate = {
      name: "Candidate",
      description: "",
      createdAt: "2026-01-01T00:00:00.000Z",
      updatedAt: "2026-01-01T00:00:00.000Z",
      owner: { id: "owner-1", name: "Owner", avatar: "" },
      tags: ["template"],
      nodes: [],
      edges: [],
    };
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="templates"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[
          { ...baseTemplate, id: "template-chat_interviewer" },
          { ...baseTemplate, id: "template-general_assistant" },
          {
            ...baseTemplate,
            id: "template-knowledge_desk-knowledge_guide",
            candidateGroup: "knowledge_desk",
          },
          {
            ...baseTemplate,
            id: "template-knowledge_desk-web_archivist",
            candidateGroup: "knowledge_desk",
          },
          {
            ...baseTemplate,
            id: "template-news_desk-feed_curator",
            candidateGroup: "news_desk",
          },
        ]}
        tabCounts={{ all: 0, pinned: 0, templates: 5 }}
        isTemplateView
        workspaceLabel="AI Company"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    // Underscored folder names become sentence-case section labels.
    const knowledgeHeader = screen.getByRole("button", {
      name: /Knowledge desk 2/i,
    });
    expect(knowledgeHeader).toBeTruthy();
    expect(screen.getByRole("button", { name: /News desk 1/i })).toBeTruthy();
    // Independent candidates are not wrapped in a section.
    expect(
      screen.queryByRole("button", { name: /Chat interviewer/i }),
    ).toBeNull();
    // Two independents + three grouped candidates.
    expect(screen.getAllByTestId("workflow-card")).toHaveLength(5);

    // Collapsing a group hides only that group's cards.
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    await user.click(knowledgeHeader);
    expect(screen.getAllByTestId("workflow-card")).toHaveLength(3);
  });

  it("keeps candidates flat when none belong to a group", () => {
    const baseTemplate = {
      name: "Candidate",
      description: "",
      createdAt: "2026-01-01T00:00:00.000Z",
      updatedAt: "2026-01-01T00:00:00.000Z",
      owner: { id: "owner-1", name: "Owner", avatar: "" },
      tags: ["template"],
      nodes: [],
      edges: [],
    };
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="templates"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[
          { ...baseTemplate, id: "template-chat_interviewer" },
          { ...baseTemplate, id: "template-insight_analyst" },
        ]}
        tabCounts={{ all: 0, pinned: 0, templates: 2 }}
        isTemplateView
        workspaceLabel="AI Company"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /desk/i })).toBeNull();
    expect(screen.getAllByTestId("workflow-card")).toHaveLength(2);
  });

  it("shows empty team sections with no cards inside", () => {
    renderTabs(
      <WorkflowGalleryTabs
        selectedTab="all"
        onSelectedTabChange={vi.fn()}
        isLoading={false}
        sortedWorkflows={[]}
        tabCounts={{ all: 0, pinned: 0, templates: 0 }}
        isTemplateView={false}
        teams={[
          { id: "team-default", slug: "acme", name: "Acme", is_default: true },
          { id: "team-eng", slug: "engineering", name: "Engineering", is_default: false },
        ]}
        workspaceLabel="Acme"
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        onImportStarterPack={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onUseTemplate={vi.fn()}
        onExportWorkflow={vi.fn()}
        onDeleteWorkflow={vi.fn()}
      />,
    );

    // Both sections are rendered even though there are no workflows.
    expect(screen.getByRole("button", { name: /Acme 0/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Engineering 0/i })).toBeTruthy();
    expect(screen.queryByTestId("workflow-card")).toBeNull();
  });
});
