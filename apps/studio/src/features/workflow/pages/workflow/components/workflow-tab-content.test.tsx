import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { WorkflowTabContent } from "./workflow-tab-content";

const { mockPublishWorkflow, mockUnpublishWorkflow } = vi.hoisted(() => ({
  mockPublishWorkflow: vi.fn(() =>
    Promise.resolve({ shareUrl: "https://example.com/chat", message: "ok" }),
  ),
  mockUnpublishWorkflow: vi.fn(() => Promise.resolve({})),
}));

vi.mock("@features/workflow/lib/workflow-storage-api", () => ({
  publishWorkflow: mockPublishWorkflow,
  unpublishWorkflow: mockUnpublishWorkflow,
  fetchCronTriggerConfig: vi.fn(() => Promise.resolve(null)),
  scheduleWorkflowFromLatestVersion: vi.fn(),
  unscheduleWorkflow: vi.fn(),
}));

afterEach(() => {
  cleanup();
});

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@xyflow/react", () => ({
  Controls: () => <div data-testid="controls" />,
  ReactFlow: ({ children }: { children?: unknown }) => (
    <div data-testid="react-flow">{children}</div>
  ),
}));

vi.mock(
  "@features/workflow/components/dialogs/confirm-delete-workflow-dialog",
  () => ({
    ConfirmDeleteWorkflowDialog: () => null,
  }),
);

vi.mock(
  "@features/workflow/pages/workflow/components/workflow-config-sheet",
  () => ({
    WorkflowConfigSheet: () => null,
  }),
);

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  deleteWorkflow: vi.fn(),
}));

vi.mock("@features/workflow/lib/mermaid-renderer", () => ({
  buildMermaidCacheKey: () => null,
  buildMermaidRenderId: () => null,
  makeMermaidSvgTransparent: (svg: string) => svg,
  renderMermaidSvg: vi.fn(),
}));

vi.mock("@features/workflow/lib/workflow-storage-helpers", () => ({
  resolveWorkflowVersionMermaidSource: () => null,
}));

const baseProps = {
  workflowId: "workflow-1",
  workflowName: "Workflow",
  versions: [],
  isLoading: false,
  loadError: null,
  isRunPending: false,
  isRunning: false,
  onRunWorkflow: vi.fn(),
  onSaveConfig: vi.fn(),
  hasCronTriggerNode: false,
  initialIsPublished: false,
  initialRequireLogin: false,
  initialShareUrl: null,
} satisfies Parameters<typeof WorkflowTabContent>[0];

describe("WorkflowTabContent", () => {
  const runnableVersion = {
    id: "version-1",
    version: "v1",
    versionNumber: 1,
    timestamp: "2026-06-20T10:00:00Z",
    message: "Uploaded from CLI",
    author: { id: "cli", name: "cli", avatar: "" },
    summary: { added: 0, removed: 0, modified: 0 },
    snapshot: { name: "Workflow", description: "", nodes: [], edges: [] },
  };

  it("shows the offboard action for regular workflows", () => {
    render(<WorkflowTabContent {...baseProps} workflowRouteRef="workflow-1" />);

    expect(screen.getByRole("button", { name: /^offboard$/i })).toBeTruthy();
  });

  it("keeps the run button disabled while a workflow is running", () => {
    const onRunWorkflow = vi.fn();

    render(
      <WorkflowTabContent
        {...baseProps}
        versions={[runnableVersion]}
        isRunning
        onRunWorkflow={onRunWorkflow}
        workflowRouteRef="workflow-1"
      />,
    );

    const runButton = screen.getByRole("button", { name: /running/i });

    expect(runButton).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Workflow run in progress. Check the latest record on the Trace tab for live status.",
    );

    fireEvent.click(runButton);

    expect(onRunWorkflow).not.toHaveBeenCalled();
  });

  it("shows the same running reminder while a run is being submitted", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        versions={[runnableVersion]}
        isRunPending
        workflowRouteRef="workflow-1"
      />,
    );

    expect(screen.getByRole("button", { name: /running/i })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Check the latest record on the Trace tab",
    );
  });

  it("shows a success result banner once a run finishes", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        versions={[runnableVersion]}
        lastRunStatus="success"
        workflowRouteRef="workflow-1"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Workflow run succeeded. See the full trace on the Trace tab.",
    );
  });

  it("shows a failure result banner once a run fails", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        versions={[runnableVersion]}
        lastRunStatus="failed"
        workflowRouteRef="workflow-1"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Workflow run failed. Check the Trace tab for details.",
    );
  });

  it("shows upload failure details and retry instructions", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        uploadError={{
          message: "Invalid script: expected ':'",
          occurredAt: "2026-07-02T10:00:00Z",
        }}
        workflowRouteRef="workflow-1"
      />,
    );

    expect(screen.getByText("Workflow upload failed")).toBeInTheDocument();
    expect(screen.getByText("Invalid script: expected ':'")).toBeInTheDocument();
    expect(
      screen.getByText(/fix the error in the workflow script or config/i),
    ).toBeInTheDocument();
  });

  it("prefers the in-progress reminder over a prior result while running", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        versions={[runnableVersion]}
        isRunning
        lastRunStatus="success"
        workflowRouteRef="workflow-1"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Workflow run in progress. Check the latest record on the Trace tab for live status.",
    );
  });

  it("opens the publish dialog and publishes with the chosen visibility", async () => {
    render(<WorkflowTabContent {...baseProps} workflowRouteRef="workflow-1" />);

    fireEvent.click(screen.getByRole("switch", { name: "Publish workflow" }));

    // Dialog appears with the visibility choices.
    expect(await screen.findByText("Publish workflow")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /workspace only/i }));
    fireEvent.click(screen.getByRole("button", { name: /^publish$/i }));

    await waitFor(() => {
      expect(mockPublishWorkflow).toHaveBeenCalledWith("workflow-1", {
        actor: "studio",
        requireLogin: true,
      });
    });
  });
});
