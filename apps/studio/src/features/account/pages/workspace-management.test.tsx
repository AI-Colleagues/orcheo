import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceManagement from "./workspace-management";

const setPageContextMock = vi.fn();
vi.mock("@/hooks/use-page-context", () => ({
  usePageContext: () => ({
    setPageContext: setPageContextMock,
    setVaultOpen: vi.fn(),
    pageContext: { page: "workspace" },
  }),
}));

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
    setPageContextMock.mockReset();
  });

  it("shows the members tab by default and switches to API Keys tab", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(setPageContextMock).toHaveBeenCalledWith({ page: "workspace" });
    expect(screen.getByTestId("members-panel")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "API Keys" }));
    expect(screen.getByTestId("api-keys-panel")).toBeInTheDocument();
  });
});
