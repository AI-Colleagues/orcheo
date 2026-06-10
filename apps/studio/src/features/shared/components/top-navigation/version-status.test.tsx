import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VersionStatus from "@/features/shared/components/top-navigation/version-status";
import { getSystemInfo } from "@/lib/api";

vi.mock("@/lib/config", () => ({
  getStudioVersion: () => "1.2.3",
}));

vi.mock("@/lib/api", () => ({
  getSystemInfo: vi.fn(),
}));

describe("VersionStatus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders versions and update reminder when update exists", async () => {
    vi.mocked(getSystemInfo).mockResolvedValueOnce({
      backend: {
        package: "orcheo-backend",
        current_version: "0.18.0",
        latest_version: "0.19.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "0.15.0",
        latest_version: "0.16.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      studio: {
        package: "orcheo-studio",
        current_version: null,
        latest_version: "1.2.4",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      checked_at: "2026-02-21T12:00:00Z",
    });

    render(<VersionStatus />);

    await waitFor(() => {
      expect(screen.getByText(/Orcheo 1.2.3/)).toBeInTheDocument();
      expect(screen.getByText(/Backend 0.18.0/)).toBeInTheDocument();
      expect(screen.getByText(/Update available/)).toBeInTheDocument();
    });
  });

  it("uses cache and skips fetch inside 24h window", async () => {
    window.localStorage.setItem(
      "orcheo.studio.system_info.v1",
      JSON.stringify({
        checkedAt: new Date().toISOString(),
        payload: {
          backend: {
            package: "orcheo-backend",
            current_version: "0.18.0",
            latest_version: "0.18.0",
            minimum_recommended_version: null,
            release_notes_url: null,
            update_available: false,
          },
          cli: {
            package: "orcheo-sdk",
            current_version: "0.15.0",
            latest_version: "0.15.0",
            minimum_recommended_version: null,
            release_notes_url: null,
            update_available: false,
          },
          studio: {
            package: "orcheo-studio",
            current_version: null,
            latest_version: "1.2.3",
            minimum_recommended_version: null,
            release_notes_url: null,
            update_available: false,
          },
          checked_at: "2026-02-21T12:00:00Z",
        },
      }),
    );

    render(<VersionStatus />);

    await waitFor(() => {
      expect(screen.getByText(/Backend 0.18.0/)).toBeInTheDocument();
    });
    expect(getSystemInfo).not.toHaveBeenCalled();
  });

  it("shows tooltip with component update details on hover", async () => {
    const user = userEvent.setup();

    vi.mocked(getSystemInfo).mockResolvedValueOnce({
      backend: {
        package: "orcheo-backend",
        current_version: "0.18.0",
        latest_version: "0.19.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "0.15.0",
        latest_version: "0.16.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      studio: {
        package: "orcheo-studio",
        current_version: null,
        latest_version: "1.2.4",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      checked_at: "2026-02-21T12:00:00Z",
    });

    render(<VersionStatus />);

    await waitFor(() => {
      expect(screen.getByText(/Update available/)).toBeInTheDocument();
    });

    const badge = screen.getByText(/Update available/);
    await user.hover(badge);

    await waitFor(() => {
      expect(
        screen.getAllByText(/Backend: 0.18.0 → 0.19.0/).length,
      ).toBeGreaterThan(0);
      expect(
        screen.getAllByText(/Orcheo: 1.2.3 → 1.2.4/).length,
      ).toBeGreaterThan(0);
      expect(
        screen.getAllByText(/orcheo install upgrade/).length,
      ).toBeGreaterThan(0);
    });
  });

  it("allows dismissing update reminders for 24h", async () => {
    const user = userEvent.setup();

    vi.mocked(getSystemInfo).mockResolvedValueOnce({
      backend: {
        package: "orcheo-backend",
        current_version: "0.18.0",
        latest_version: "0.19.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "0.15.0",
        latest_version: "0.16.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      studio: {
        package: "orcheo-studio",
        current_version: null,
        latest_version: "1.2.4",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      checked_at: "2026-02-21T12:00:00Z",
    });

    render(<VersionStatus />);

    await waitFor(() => {
      expect(screen.getByText(/Update available/)).toBeInTheDocument();
    });

    await user.hover(screen.getByText(/Update available/));

    await waitFor(() => {
      expect(
        screen.getAllByRole("button", { name: /Remind me tomorrow/ }).length,
      ).toBeGreaterThan(0);
    });

    const [dismissButton] = screen.getAllByRole("button", {
      name: /Remind me tomorrow/,
    });
    await user.click(dismissButton);

    expect(screen.queryByText(/Update available/)).not.toBeInTheDocument();
    expect(
      Date.parse(
        window.localStorage.getItem("orcheo.studio.system_info.dismissed.v1") ??
          "",
      ),
    ).not.toBeNaN();
  });
});
