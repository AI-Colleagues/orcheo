import { act, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { onboardCandidateAsWorkflow } from "@features/workflow/lib/workflow-storage";
import { getCandidateBadgeDefinition } from "@features/workflow/data/templates/candidate-badges";
import { useWorkflowGalleryActions } from "./use-workflow-gallery-actions";

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  onboardCandidateAsWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
}));

vi.mock("@features/workflow/data/templates/candidate-badges", () => ({
  getCandidateBadgeDefinition: vi.fn(),
}));

vi.mock("@/hooks/use-toast", () => ({ toast: vi.fn() }));

const mockedOnboard = vi.mocked(onboardCandidateAsWorkflow);
const mockedBadge = vi.mocked(getCandidateBadgeDefinition);

const wrapper = ({ children }: { children: ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

const DEFAULT_TEAM = {
  id: "team-default",
  slug: "acme",
  name: "Acme",
  is_default: true,
};
const SALES_TEAM = {
  id: "team-sales",
  slug: "sales",
  name: "Sales",
  is_default: false,
};

describe("useWorkflowGalleryActions onboarding", () => {
  beforeEach(() => {
    mockedOnboard.mockReset();
    mockedOnboard.mockResolvedValue({
      id: "wf-1",
      name: "Insight Analyst",
    } as never);
    mockedBadge.mockReset();
    mockedBadge.mockReturnValue({
      candidateId: "insight-analyst",
      name: "Insight Analyst",
    } as never);
  });

  it("onboards directly into the default team when only one team exists", async () => {
    const { result } = renderHook(
      () =>
        useWorkflowGalleryActions({
          setSelectedTab: vi.fn(),
          teams: [DEFAULT_TEAM],
        }),
      { wrapper },
    );

    await act(async () => {
      await result.current.handleUseTemplate("template-insight");
    });

    expect(result.current.onboardTarget).toBeNull();
    expect(mockedOnboard).toHaveBeenCalledWith("insight-analyst", undefined);
  });

  it("prompts for a team and onboards into the chosen one", async () => {
    const { result } = renderHook(
      () =>
        useWorkflowGalleryActions({
          setSelectedTab: vi.fn(),
          teams: [DEFAULT_TEAM, SALES_TEAM],
        }),
      { wrapper },
    );

    await act(async () => {
      await result.current.handleUseTemplate("template-insight");
    });

    // The prompt opens instead of onboarding immediately.
    expect(mockedOnboard).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(result.current.onboardTarget).toEqual({
        candidateId: "insight-analyst",
        candidateName: "Insight Analyst",
      }),
    );

    await act(async () => {
      await result.current.confirmOnboardTeam("team-sales");
    });

    expect(mockedOnboard).toHaveBeenCalledWith("insight-analyst", "team-sales");
    expect(result.current.onboardTarget).toBeNull();
  });
});
