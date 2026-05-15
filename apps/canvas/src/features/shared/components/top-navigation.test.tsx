import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import TopNavigation from "@/features/shared/components/top-navigation";

vi.mock("@/features/shared/components/top-navigation/canvas-brand", () => ({
  default: () => <div data-testid="canvas-brand" />,
}));

vi.mock(
  "@/features/shared/components/top-navigation/active-workspace-indicator",
  () => ({
    default: () => <div data-testid="active-workspace-indicator" />,
  }),
);

vi.mock("@/features/shared/components/top-navigation/version-status", () => ({
  default: () => <div data-testid="version-status" />,
}));

vi.mock("@/features/shared/components/top-navigation/account-menu", () => ({
  default: () => <div data-testid="account-menu" />,
}));

afterEach(() => {
  cleanup();
});

describe("TopNavigation", () => {
  it("keeps the header at a fixed height", () => {
    render(
      <MemoryRouter>
        <TopNavigation />
      </MemoryRouter>,
    );

    expect(screen.getByRole("banner")).toHaveClass("h-14", "shrink-0");
  });
});
