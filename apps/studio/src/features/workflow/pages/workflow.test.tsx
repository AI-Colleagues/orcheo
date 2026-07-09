import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowPage from "./workflow";

const controllerMock = vi.fn();
const getWorkflowByIdMock = vi.fn();
const listWorkflowsMock = vi.fn();

vi.mock("@/hooks/use-page-context", () => ({
  usePageContext: () => ({
    setPageContext: vi.fn(),
  }),
}));

vi.mock(
  "@features/workflow/pages/workflow/hooks/controller/use-workflow-controller",
  () => ({
    useWorkflowController: (...args: unknown[]) => controllerMock(...args),
  }),
);

vi.mock("@features/workflow/pages/workflow/components/workflow-layout", () => ({
  WorkflowLayout: ({
    workflowProps,
    topNavigationProps,
  }: {
    workflowProps: { workflowId: string | null };
    topNavigationProps: { currentWorkflow: { name: string } };
  }) => (
    <div data-testid="workflow-layout">
      <span data-testid="workflow-id">{workflowProps.workflowId ?? "new"}</span>
      <span data-testid="workflow-name">
        {topNavigationProps.currentWorkflow.name}
      </span>
    </div>
  ),
}));

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  getWorkflowById: (...args: unknown[]) => getWorkflowByIdMock(...args),
  listWorkflows: (...args: unknown[]) => listWorkflowsMock(...args),
}));

describe("WorkflowPage", () => {
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

    render(<WorkflowPage workflowId="insight-analyst" />);

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
