import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import StudioBrand from "@/features/shared/components/top-navigation/studio-brand";

afterEach(() => {
  cleanup();
});

describe("StudioBrand", () => {
  it("renders the brand title, subtitle, and beta tooltip", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <StudioBrand />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("link", { name: /orcheo.*by ai colleagues/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();

    await user.hover(screen.getByRole("button", { name: /beta badge/i }));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Beta Disclaimer & Terms: Orcheo Cloud is a free beta for evaluation and testing, provided as-is. Data, workflows, and credentials may be reset, deleted, or not migrated, so do not use it as your only storage for critical production data; read the full terms.",
    );
    for (const link of screen.getAllByRole("link", { name: /full terms/i })) {
      expect(link).toHaveAttribute("href", "https://ai-colleagues.com/terms");
    }
  });
});
