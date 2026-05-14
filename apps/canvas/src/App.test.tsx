import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getActiveWorkspace: vi.fn().mockResolvedValue({
      name: "AI Company",
      slug: "ai-company",
      role: "owner",
    }),
    getMyWorkspaces: vi.fn().mockResolvedValue({ memberships: [] }),
  };
});

describe("App", () => {
  it("renders the Orcheo navigation", () => {
    render(<App />);
    expect(
      screen.getByRole("link", { name: /Orcheo.*by AI Colleagues/i }),
    ).toBeInTheDocument();
  });
});
