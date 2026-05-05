import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActiveTenantIndicator from "@/features/shared/components/top-navigation/active-tenant-indicator";
import { getActiveTenant } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getActiveTenant: vi.fn(),
}));

describe("ActiveTenantIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the active tenant slug when available", async () => {
    vi.mocked(getActiveTenant).mockResolvedValueOnce({
      tenant_id: "tenant-1",
      slug: "acme",
      name: "Acme",
      role: "owner",
    });

    render(<ActiveTenantIndicator />);

    await waitFor(() => {
      expect(screen.getByText("Tenant")).toBeInTheDocument();
      expect(screen.getByText("acme")).toBeInTheDocument();
    });
  });

  it("stays hidden when the tenant cannot be resolved", async () => {
    vi.mocked(getActiveTenant).mockRejectedValueOnce(new Error("unavailable"));

    render(<ActiveTenantIndicator />);

    await waitFor(() => {
      expect(getActiveTenant).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId("active-tenant-indicator")).not.toBeInTheDocument();
  });
});
