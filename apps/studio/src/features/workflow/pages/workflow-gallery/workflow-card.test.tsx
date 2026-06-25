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

const teamScopedWorkflow = {
  ...colleagueWorkflow,
  id: "workflow-2",
  teamId: "team-sales",
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

const updateAvailableWorkflow = {
  ...onboardedCandidateWorkflow,
  versions: [
    {
      id: "version-1",
      candidateSource: {
        source: "candidate-onboard",
        candidateId: "insight-analyst",
        candidateHandle: "insight-analyst",
        candidateVersion: "1.0.0",
      },
    },
  ],
} satisfies Parameters<typeof WorkflowCard>[0]["workflow"];

const createHandlers = () => ({
  onOpenWorkflow: vi.fn(),
  onUseTemplate: vi.fn(),
  onExportWorkflow: vi.fn(),
  onDeleteWorkflow: vi.fn(),
  onUpdateCandidateWorkflow: vi.fn(),
});

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  setCandidateBadges([
    {
      id: "template-insight-analyst",
      candidateId: "insight-analyst",
      name: "Insight Analyst",
      handle: "insight-analyst",
      subtitle: "AI Insights & Analytics",
      description:
        "Detects themes from text data using advanced thematic coding frameworks.",
      avatar: "avatar-03",
      version: "1.1.0",
      updates: [
        {
          version: "1.1.0",
          summary: "Adds stronger evidence checks.",
          migration: "Review prompt overrides before updating.",
        },
      ],
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

  it("opens team-scoped colleagues by immutable workflow id", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={teamScopedWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.click(screen.getByTestId("workflow-card"));

    expect(handlers.onOpenWorkflow).toHaveBeenCalledWith(teamScopedWorkflow.id);
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

  it("renders a seeded avatar image for colleague workflows without an explicit avatar", () => {
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={colleagueWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
  });

  it("renders the avatar image when an avatar id is stored on the workflow", () => {
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={{
          ...colleagueWorkflow,
          avatarEmoji: "avatar-05",
        }}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
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

  it("renders the candidate avatar image after onboarding", () => {
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={onboardedCandidateWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
  });

  it("shows candidate update details and confirms selected workflow update", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={updateAvailableWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /update insight analyst to candidate version 1.1.0/i,
      }),
    );

    expect(screen.getByText("Update candidate version")).toBeInTheDocument();
    expect(
      screen.getByText("Adds stronger evidence checks."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Review prompt overrides before updating."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^update$/i }));

    expect(handlers.onUpdateCandidateWorkflow).toHaveBeenCalledWith(
      updateAvailableWorkflow.id,
      "insight-analyst",
    );
  });

  it("shows compact candidate update context on hover", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={updateAvailableWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.hover(
      screen.getByRole("button", {
        name: /update insight analyst to candidate version 1.1.0/i,
      }),
    );

    expect(
      await screen.findAllByText(/candidate version 1.0.0 to 1.1.0/i),
    ).not.toHaveLength(0);
    expect(
      screen.getAllByText("Adds stronger evidence checks."),
    ).not.toHaveLength(0);
  });

  it("cancels candidate update without calling the update action", async () => {
    const user = userEvent.setup();
    const handlers = createHandlers();

    render(
      <WorkflowCard
        workflow={updateAvailableWorkflow}
        isTemplate={false}
        workspaceLabel="AI Company"
        {...handlers}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /update insight analyst to candidate version 1.1.0/i,
      }),
    );
    await user.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(handlers.onUpdateCandidateWorkflow).not.toHaveBeenCalled();
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
});
