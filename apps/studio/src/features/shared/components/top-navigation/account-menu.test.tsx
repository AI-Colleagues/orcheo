import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import AccountMenu from "@/features/shared/components/top-navigation/account-menu";

vi.mock("@features/auth/lib/auth-session", () => ({
  clearAuthSession: vi.fn(),
  getAuthenticatedUserProfile: () => ({
    avatar: null,
    email: "shaojie@example.com",
    name: "Shaojie Jiang",
    subject: "user-1",
  }),
}));

vi.mock("@/lib/workspace-session", () => ({
  getSelectedWorkspaceSlug: () => "default",
}));

vi.mock("@features/workflow/components/dialogs/credentials-vault", () => ({
  default: () => <div data-testid="credentials-vault" />,
}));

afterEach(() => {
  cleanup();
});

describe("AccountMenu", () => {
  it("links to the GitHub issue chooser for feedback and reports", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AccountMenu credentials={[]} isCredentialsLoading={false} />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /account menu/i }));

    const feedbackLink = await screen.findByRole("menuitem", {
      name: /feedback & issues/i,
    });

    expect(feedbackLink).toHaveAttribute(
      "href",
      "https://github.com/AI-Colleagues/orcheo/issues/new/choose",
    );
    expect(feedbackLink).toHaveAttribute("target", "_blank");
    expect(feedbackLink).toHaveAttribute("rel", "noreferrer");
  });
});
