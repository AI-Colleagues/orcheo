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
    getMyWorkspaces: vi.fn().mockResolvedValue({
      memberships: [
        {
          workspace_id: "11111111-1111-1111-1111-111111111111",
          slug: "ai-company",
          name: "AI Company",
          role: "owner",
          status: "active",
        },
      ],
    }),
  };
});

describe("App", () => {
  it("renders the Orcheo navigation", async () => {
    render(<App />);
    expect(
      await screen.findByRole("link", { name: /Orcheo.*by AI Colleagues/i }),
    ).toBeInTheDocument();
  });
});
