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
  onRunWorkflow: vi.fn(),
  onSaveConfig: vi.fn(),
  hasCronTriggerNode: false,
  initialIsPublished: false,
  initialShareUrl: null,
} satisfies Parameters<typeof WorkflowTabContent>[0];

describe("WorkflowTabContent", () => {
  it("shows the offboard action for regular workflows", () => {
    render(<WorkflowTabContent {...baseProps} workflowRouteRef="workflow-1" />);

    expect(screen.getByRole("button", { name: /^offboard$/i })).toBeTruthy();
  });
});
