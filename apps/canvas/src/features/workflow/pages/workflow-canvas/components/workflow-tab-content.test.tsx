import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { WorkflowTabContent } from "./workflow-tab-content";

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@xyflow/react", () => ({
  Controls: () => <div data-testid="controls" />,
  ReactFlow: ({ children }: { children?: unknown }) => (
    <div data-testid="react-flow">{children}</div>
  ),
}));

vi.mock("@features/workflow/components/dialogs/confirm-delete-workflow-dialog", () => ({
  ConfirmDeleteWorkflowDialog: () => null,
}));

vi.mock("@features/workflow/pages/workflow-canvas/components/workflow-config-sheet", () => ({
  WorkflowConfigSheet: () => null,
}));

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
  onRunWorkflow: vi.fn(),
  onSaveConfig: vi.fn(),
  hasCronTriggerNode: false,
  initialIsPublished: false,
  initialShareUrl: null,
} satisfies Parameters<typeof WorkflowTabContent>[0];

describe("WorkflowTabContent", () => {
  it("hides the delete action for the managed vibe workflow", () => {
    render(
      <WorkflowTabContent
        {...baseProps}
        workflowRouteRef="orcheo-vibe-agent"
      />,
    );

    expect(screen.queryByRole("button", { name: /^delete$/i })).toBeNull();
  });

  it("shows the delete action for regular workflows", () => {
    render(<WorkflowTabContent {...baseProps} workflowRouteRef="workflow-1" />);

    expect(screen.getByRole("button", { name: /^delete$/i })).toBeTruthy();
  });
});
