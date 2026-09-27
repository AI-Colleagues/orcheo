import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getSystemFeatures } from "@/lib/api";
import AppSidebar from "./app-sidebar";

vi.mock("@/lib/api", () => ({ getSystemFeatures: vi.fn() }));
vi.mock("./profile-menu", () => ({ default: () => null }));

const renderSidebar = () =>
  render(
    <MemoryRouter initialEntries={["/acme"]}>
      <AppSidebar
        collapsed={false}
        onToggleCollapsed={vi.fn()}
        onOpenVault={vi.fn()}
      />
    </MemoryRouter>,
  );

describe("AppSidebar Hosted Apps entry", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.mocked(getSystemFeatures).mockReset();
  });

  it("shows Apps when Hosted Apps are enabled", async () => {
    vi.mocked(getSystemFeatures).mockResolvedValue({
      hosted_apps_enabled: true,
    });
    renderSidebar();

    expect(await screen.findByRole("link", { name: "Apps" })).toHaveAttribute(
      "href",
      "/acme/apps",
    );
    expect(getSystemFeatures).toHaveBeenCalledWith("acme");
  });

  it("hides Apps when Hosted Apps are disabled", async () => {
    vi.mocked(getSystemFeatures).mockResolvedValue({
      hosted_apps_enabled: false,
    });
    renderSidebar();

    await waitFor(() => expect(getSystemFeatures).toHaveBeenCalledWith("acme"));
    expect(
      screen.queryByRole("link", { name: "Apps" }),
    ).not.toBeInTheDocument();
  });
});
