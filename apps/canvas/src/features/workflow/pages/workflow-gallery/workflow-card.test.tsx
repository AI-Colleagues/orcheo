import { describe, expect, it, vi } from "vitest";
import { afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WorkflowCard } from "./workflow-card";

vi.mock("@/hooks/use-toast", () => ({
  toast: vi.fn(),
}));

vi.mock("./workflow-thumbnail", () => ({
  WorkflowThumbnail: () => <div data-testid="workflow-thumbnail" />,
}));

const workflow = {
  id: "workflow-1",
  handle: "support-triage",
  name: "Support triage",
  description: "Routes inbound requests.",
  createdAt: "2026-01-01T00:00:00.000Z",
  updatedAt: "2026-01-02T00:00:00.000Z",
  owner: {
    id: "owner-1",
    name: "Owner",
    avatar: "https://example.com/avatar.png",
  },
  tags: ["support", "triage"],
  nodes: [],
  edges: [],
} satisfies Parameters<typeof WorkflowCard>[0]["workflow"];

const managedWorkflow = {
  ...workflow,
  handle: "orcheo-vibe-agent",
  name: "Orcheo Vibe",
} satisfies Parameters<typeof WorkflowCard>[0]["workflow"];

const createHandlers = () => ({
  onOpenWorkflow: vi.fn(),
  onUseTemplate: vi.fn(),
  onExportWorkflow: vi.fn(),
  onDeleteWorkflow: vi.fn(),
});

afterEach(() => {
  cleanup();
});

describe("WorkflowCard", () => {
  it("opens workflow when non-template card body is clicked", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    await user.click(screen.getByTestId("workflow-card"));

    expect(handlers.onOpenWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenWorkflow).toHaveBeenCalledWith(workflow.handle);
  });

  it("does not trigger card navigation when favorite button is clicked", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    const favoriteButton = screen.getByRole("button", {
      name: /favorite workflow/i,
    });
    await user.click(favoriteButton);

    expect(handlers.onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("opens workflow exactly once from edit button", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    await user.click(screen.getByRole("button", { name: /^edit$/i }));

    expect(handlers.onOpenWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenWorkflow).toHaveBeenCalledWith(workflow.handle);
  });

  it("keeps dropdown actions from triggering card navigation", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: /^export$/i }),
    );

    expect(handlers.onExportWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("shows Export action label without JSON for workflows", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );

    expect(
      await screen.findByRole("menuitem", { name: /^export$/i }),
    ).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /export json/i })).toBeNull();
  });

  it("shows Export action label without JSON for templates", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(<WorkflowCard workflow={workflow} isTemplate {...handlers} />);

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );

    expect(
      await screen.findByRole("menuitem", { name: /^export$/i }),
    ).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /export json/i })).toBeNull();
  });

  it("does not trigger card navigation for keyboard events from child actions", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    const favoriteButton = screen.getByRole("button", {
      name: /favorite workflow/i,
    });
    favoriteButton.focus();
    await user.keyboard("{Enter}");

    expect(handlers.onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("requires confirmation before deleting a workflow", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard workflow={workflow} isTemplate={false} {...handlers} />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: /^delete$/i }),
    );

    expect(handlers.onDeleteWorkflow).not.toHaveBeenCalled();
    expect(screen.getByText(/delete workflow\?/i)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^delete$/i }));

    expect(handlers.onDeleteWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onDeleteWorkflow).toHaveBeenCalledWith(
      workflow.id,
      workflow.name,
    );
  });

  it("hides delete actions for the managed vibe workflow", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={managedWorkflow}
        isTemplate={false}
        {...handlers}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );

    expect(
      screen.queryByRole("menuitem", { name: /^delete$/i }),
    ).toBeNull();
    expect(screen.queryByText(/delete workflow\?/i)).toBeNull();
  });
});
