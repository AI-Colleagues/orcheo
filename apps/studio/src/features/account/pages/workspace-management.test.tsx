import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceManagement from "./workspace-management";

vi.mock("@features/account/pages/workspace-members", () => ({
  default: () => <div data-testid="members-panel" />,
}));

vi.mock("@features/account/pages/service-tokens", () => ({
  default: () => <div data-testid="api-keys-panel" />,
}));

const renderPage = () =>
  render(
    <MemoryRouter>
      <WorkspaceManagement />
    </MemoryRouter>,
  );

describe("WorkspaceManagement", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows the members tab by default and switches to API Keys tab", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByTestId("members-panel")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "API Keys" }));
    expect(screen.getByTestId("api-keys-panel")).toBeInTheDocument();
  });
});
