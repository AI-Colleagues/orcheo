import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCandidateBadges } from "@features/workflow/data/templates/candidate-badges";
import { WorkflowCard } from "./workflow-card";
import {
  WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME,
  WORKFLOW_GALLERY_CARD_ASPECT_RATIO,
} from "./workflow-card-size";

vi.mock("@/hooks/use-toast", () => ({
  toast: vi.fn(),
}));

const colleagueWorkflow = {
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

const candidateWorkflow = {
  id: "template-insight-analyst",
  handle: "insight-analyst",
  name: "Insight Analyst",
  description:
    "Detects themes from text data using advanced thematic coding frameworks.",
  createdAt: "2026-01-01T00:00:00.000Z",
  updatedAt: "2026-01-02T00:00:00.000Z",
  owner: {
    id: "owner-1",
    name: "Owner",
    avatar: "https://example.com/avatar.png",
  },
  tags: ["template", "python", "agent"],
  nodes: [],
  edges: [],
} satisfies Parameters<typeof WorkflowCard>[0]["workflow"];

const onboardedCandidateWorkflow = {
  ...colleagueWorkflow,
  id: "workflow-onboarded-insight",
  name: "Insight Analyst",
  description:
    "Detects themes from text data using advanced thematic coding frameworks.",
  owner: {
    id: "owner-2",
    name: "Owner",
    avatar: "https://example.com/owner-avatar.png",
  },
  versions: [
    {
      id: "version-1",
      templateId: "template-insight-analyst",
    },
  ],
} satisfies Parameters<typeof WorkflowCard>[0]["workflow"];

const managedWorkflow = {
  ...colleagueWorkflow,
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

beforeEach(() => {
  setCandidateBadges([
    {
      id: "template-insight-analyst",
      name: "Insight Analyst",
      handle: "insight-analyst",
      subtitle: "AI Insights & Analytics",
      description:
        "Detects themes from text data using advanced thematic coding frameworks.",
      emoji: "👨‍🎓",
    },
  ]);
});

describe("WorkflowCard", () => {
  it("uses the portrait gallery aspect ratio", () => {
    expect(WORKFLOW_GALLERY_CARD_ASPECT_RATIO).toBeCloseTo(53.98 / 85.6, 6);
  });

  it("opens workflow when a colleague card body is clicked", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={colleagueWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    const card = screen.getByTestId("workflow-card");
    expect(card.className).toContain(WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME);

    await user.click(card);

    expect(handlers.onOpenWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenWorkflow).toHaveBeenCalledWith(
      colleagueWorkflow.handle,
    );
  });

  it("renders colleague badge copy without creator metadata or edit actions", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={colleagueWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByText("AI Company")).toBeInTheDocument();
    expect(screen.getByText("@support-triage")).toBeInTheDocument();
    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("Routes inbound requests.")).toBeInTheDocument();
    expect(screen.queryByText(/id:/i)).toBeNull();
    expect(screen.queryByText(/handle:/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: /star workflow/i }));

    expect(handlers.onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("renders candidate badge copy and onboard action", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={candidateWorkflow}
        isTemplate
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByText("Candidate")).toBeInTheDocument();
    expect(screen.getByText("@insight-analyst")).toBeInTheDocument();
    expect(screen.getByText("AI Insights & Analytics")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Detects themes from text data using advanced thematic coding frameworks.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /star workflow/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: /onboard/i }));

    expect(handlers.onUseTemplate).toHaveBeenCalledTimes(1);
    expect(handlers.onUseTemplate).toHaveBeenCalledWith(candidateWorkflow.id);
  });

  it("keeps the candidate emoji after onboarding", () => {
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={onboardedCandidateWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByText("👨‍🎓")).toBeInTheDocument();
  });

  it("keeps dropdown transfer actions from triggering navigation", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={colleagueWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: /^transfer$/i }),
    );

    expect(handlers.onExportWorkflow).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("hides offboard actions for the managed vibe workflow", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={managedWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /workflow actions/i,
      }),
    );

    expect(screen.queryByRole("menuitem", { name: /^offboard$/i })).toBeNull();
  });
});
