import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { WorkflowCanvasLayout } from "./workflow-canvas-layout";

const canvasChatBubbleMock = vi.fn(() => <div data-testid="chat-bubble" />);

vi.mock("@features/shared/components/top-navigation", () => ({
  default: () => <div data-testid="top-navigation" />,
}));

vi.mock("@features/workflow/components/panels/workflow-tabs", () => ({
  default: () => <div data-testid="workflow-tabs" />,
}));

vi.mock("@features/chatkit/components/canvas-chat-bubble", () => ({
  CanvasChatBubble: (props: unknown) => canvasChatBubbleMock(props),
}));

vi.mock(
  "@features/workflow/pages/workflow-canvas/components/workflow-tab-content",
  () => ({
    WorkflowTabContent: () => <div>workflow-panel</div>,
  }),
);

vi.mock(
  "@features/workflow/pages/workflow-canvas/components/trace-tab-content",
  () => ({
    TraceTabContent: () => <div>trace-panel</div>,
  }),
);

vi.mock(
  "@features/workflow/pages/workflow-canvas/components/readiness-tab-content",
  () => ({
    ReadinessTabContent: () => <div>readiness-panel</div>,
  }),
);

vi.mock(
  "@features/workflow/pages/workflow-canvas/components/settings-tab-content",
  () => ({
    SettingsTabContent: () => <div>settings-panel</div>,
  }),
);

describe("WorkflowCanvasLayout", () => {
  afterEach(() => {
    cleanup();
  });

  it("passes workflow chatkit prompts and models to the canvas chat bubble", () => {
    render(
      <WorkflowCanvasLayout
        topNavigationProps={
          {
            currentWorkflow: { name: "Workflow", id: "wf-1" },
          } as never
        }
        tabsProps={{
          activeTab: "workflow",
          onTabChange: vi.fn(),
          readinessAlertCount: 0,
        }}
        workflowProps={{} as never}
        traceProps={{} as never}
        readinessProps={{} as never}
        settingsProps={{} as never}
        chat={
          {
            isChatOpen: true,
            chatTitle: "Workflow",
            user: { id: "user-1", name: "User", avatar: "" },
            ai: { id: "ai-1", name: "AI", avatar: "" },
            activeChatNodeId: "chat-node-1",
            workflowId: "wf-1",
            chatkitWorkflowId: "wf-uuid-1",
            backendBaseUrl: "http://localhost:8000",
            startScreenPrompts: [
              {
                label: "Summarize",
                prompt: "Summarize the latest run.",
                icon: "search",
              },
            ],
            supportedModels: [
              { id: "openai:gpt-5", label: "GPT-5", default: true },
            ],
            handleChatResponseStart: vi.fn(),
            handleChatResponseEnd: vi.fn(),
            handleChatClientTool: vi.fn(),
            getClientSecret: vi.fn(),
            refreshSession: vi.fn(),
            sessionStatus: "ready",
            sessionError: null,
            handleCloseChat: vi.fn(),
            setIsChatOpen: vi.fn(),
          } as never
        }
      />,
    );

    expect(screen.getByTestId("chat-bubble")).toBeInTheDocument();
    expect(canvasChatBubbleMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startScreenPrompts: [
          {
            label: "Summarize",
            prompt: "Summarize the latest run.",
            icon: "search",
          },
        ],
        supportedModels: [
          { id: "openai:gpt-5", label: "GPT-5", default: true },
        ],
        chatkitWorkflowId: "wf-uuid-1",
      }),
    );
  });

  it("keeps the workflow tab mounted but hidden when trace is active", () => {
    render(
      <WorkflowCanvasLayout
        topNavigationProps={
          {
            currentWorkflow: { name: "Workflow", id: "wf-1" },
          } as never
        }
        tabsProps={{
          activeTab: "trace",
          onTabChange: vi.fn(),
          readinessAlertCount: 0,
        }}
        workflowProps={{} as never}
        traceProps={{} as never}
        readinessProps={{} as never}
        settingsProps={{} as never}
        chat={null}
      />,
    );

    const workflowPanel = screen
      .getByText("workflow-panel")
      .closest('[role="tabpanel"]');

    expect(workflowPanel).toHaveAttribute("data-state", "inactive");
    expect(workflowPanel).toHaveClass("data-[state=inactive]:hidden");
    expect(screen.getByText("trace-panel")).toBeInTheDocument();
  });
});
