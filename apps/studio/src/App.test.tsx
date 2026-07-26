import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

// The app shell renders behind the first-party auth gate; this test exercises
// navigation, not login, so it runs with auth disabled (as a self-host/dev
// deployment would).
beforeAll(() => {
  vi.stubEnv("VITE_ORCHEO_AUTH_DISABLED", "true");
});

afterAll(() => {
  vi.unstubAllEnvs();
});

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
    getSystemInfo: vi.fn().mockResolvedValue({
      core: {
        package: "orcheo",
        current_version: "0.1.0",
        latest_version: "0.1.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      backend: {
        package: "orcheo-backend",
        current_version: "0.1.0",
        latest_version: "0.1.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "0.1.0",
        latest_version: "0.1.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      studio: {
        package: "orcheo-studio",
        current_version: "0.1.0",
        latest_version: "0.1.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      checked_at: "2026-07-26T00:00:00Z",
      uploads_allowed: true,
    }),
  };
});

vi.mock("@/hooks/use-credential-vault", () => ({
  default: () => ({
    credentials: [],
    isLoading: false,
    onAddCredential: vi.fn(),
    onUpdateCredential: vi.fn(),
    onDeleteCredential: vi.fn(),
    onRevealCredentialSecret: vi.fn(),
  }),
}));

vi.mock("@features/workflow/pages/workflow-gallery", () => ({
  default: () => <div>Workflow gallery</div>,
}));

describe("App", () => {
  it("renders the Orcheo navigation", async () => {
    render(<App />);
    expect(
      await screen.findByRole("link", { name: /Orcheo.*by AI Colleagues/i }),
    ).toBeInTheDocument();
    expect(await screen.findByText("AI Company")).toBeInTheDocument();
  });
});
