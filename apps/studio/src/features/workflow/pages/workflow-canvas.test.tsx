import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowCanvas from "./workflow-canvas";

const controllerMock = vi.fn();
const getWorkflowByIdMock = vi.fn();
const listWorkflowsMock = vi.fn();

vi.mock("@/hooks/use-page-context", () => ({
  usePageContext: () => ({
    setPageContext: vi.fn(),
  }),
}));

vi.mock(
  "@features/workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-controller",
  () => ({
    useWorkflowCanvasController: (...args: unknown[]) =>
      controllerMock(...args),
  }),
);

vi.mock(
  "@features/workflow/pages/workflow-canvas/components/workflow-canvas-layout",
  () => ({
    WorkflowCanvasLayout: ({
      workflowProps,
      topNavigationProps,
    }: {
      workflowProps: { workflowId: string | null };
      topNavigationProps: { currentWorkflow: { name: string } };
    }) => (
      <div data-testid="workflow-canvas-layout">
        <span data-testid="workflow-id">
          {workflowProps.workflowId ?? "new"}
        </span>
        <span data-testid="workflow-name">
          {topNavigationProps.currentWorkflow.name}
        </span>
      </div>
    ),
  }),
);

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  getWorkflowById: (...args: unknown[]) => getWorkflowByIdMock(...args),
  listWorkflows: (...args: unknown[]) => listWorkflowsMock(...args),
}));

describe("WorkflowCanvas", () => {
  beforeEach(() => {
    controllerMock.mockReset();
    getWorkflowByIdMock.mockReset();
    listWorkflowsMock.mockReset();
  });

  it("resolves a workflow handle to the underlying workflow id before mounting", async () => {
    getWorkflowByIdMock.mockResolvedValue(undefined);
    listWorkflowsMock.mockResolvedValue([
      { id: "wf-uuid-1", handle: "insight-analyst" },
    ]);

    controllerMock.mockImplementation((workflowId?: string) => ({
      layoutProps: {
        workflowProps: { workflowId: workflowId ?? null },
        topNavigationProps: {
          currentWorkflow: {
            name: workflowId ?? "New Workflow",
          },
        },
        tabsProps: { activeTab: "workflow" },
      },
    }));

    render(<WorkflowCanvas workflowId="insight-analyst" />);

    await waitFor(() => {
      expect(controllerMock).toHaveBeenCalledWith(
        "wf-uuid-1",
        "insight-analyst",
      );
    });

    expect(getWorkflowByIdMock).toHaveBeenCalledWith("insight-analyst");
    expect(listWorkflowsMock).toHaveBeenCalledWith({ forceRefresh: true });
    expect(screen.getByTestId("workflow-id")).toHaveTextContent("wf-uuid-1");
    expect(screen.getByTestId("workflow-name")).toHaveTextContent("wf-uuid-1");
  });
});
