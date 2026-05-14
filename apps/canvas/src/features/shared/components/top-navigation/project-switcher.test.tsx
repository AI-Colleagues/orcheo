import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ProjectSwitcher from "@/features/shared/components/top-navigation/project-switcher";
import { listWorkflows } from "@features/workflow/lib/workflow-storage";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage.types";

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  listWorkflows: vi.fn(),
  getWorkflowRouteRef: (workflow: { id: string; handle?: string }) =>
    workflow.handle ?? workflow.id,
  WORKFLOW_STORAGE_EVENT: "orcheo:workflow-storage.updated",
}));

const buildStoredWorkflow = (
  id: string,
  name: string,
  updatedAt: string,
  handle?: string,
): StoredWorkflow => ({
  id,
  handle,
  name,
  description: "",
  createdAt: "2026-03-01T10:00:00.000Z",
  updatedAt,
  owner: {
    id: "user-1",
    name: "User",
    avatar: "",
  },
  tags: [],
  nodes: [],
  edges: [],
  versions: [],
});

describe("ProjectSwitcher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows recent colleagues from storage instead of hardcoded project names", async () => {
    vi.mocked(listWorkflows).mockResolvedValueOnce([
      buildStoredWorkflow(
        "workflow-1",
        "Simple Agent Copy",
        "2026-03-07T10:00:00.000Z",
        "simple-agent-copy",
      ),
      buildStoredWorkflow(
        "workflow-2",
        "Release Assistant",
        "2026-03-06T10:00:00.000Z",
      ),
    ]);

    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ProjectSwitcher />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /my projects/i }));

    await waitFor(() => {
      expect(screen.getByText("Simple Agent Copy")).toBeInTheDocument();
      expect(screen.getByText("Release Assistant")).toBeInTheDocument();
    });

    expect(screen.queryByText("Marketing Automations")).not.toBeInTheDocument();

    expect(
      screen.getByRole("link", { name: "Simple Agent Copy" }),
    ).toHaveAttribute("href", "/workflow-canvas/simple-agent-copy");
  });

  it("shows an empty state when no colleagues exist", async () => {
    vi.mocked(listWorkflows).mockResolvedValueOnce([]);

    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ProjectSwitcher />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /my projects/i }));

    await waitFor(() => {
      expect(screen.getByText("No colleagues yet")).toBeInTheDocument();
    });
  });
});
