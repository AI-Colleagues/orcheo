import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AboutDialog from "./about-dialog";

const getSystemInfoMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  getSystemInfo: getSystemInfoMock,
}));

describe("AboutDialog", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getSystemInfoMock.mockReset();
  });

  it("checks for updates while closed so the shell can show an ambient notice", async () => {
    getSystemInfoMock.mockResolvedValue({
      core: {
        package: "orcheo",
        current_version: "1.0.0",
        latest_version: "1.0.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      backend: {
        package: "orcheo-backend",
        current_version: "1.0.0",
        latest_version: "1.1.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "1.0.0",
        latest_version: "1.0.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      studio: {
        package: "orcheo-studio",
        current_version: "0.24.7",
        latest_version: "0.24.7",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: false,
      },
      checked_at: "2026-07-26T00:00:00Z",
      uploads_allowed: true,
    });
    const onUpdateAvailableChange = vi.fn();

    render(
      <AboutDialog
        open={false}
        onOpenChange={vi.fn()}
        onUpdateAvailableChange={onUpdateAvailableChange}
      />,
    );

    await waitFor(() => {
      expect(getSystemInfoMock).toHaveBeenCalledOnce();
      expect(onUpdateAvailableChange).toHaveBeenLastCalledWith(true);
    });
  });
});
