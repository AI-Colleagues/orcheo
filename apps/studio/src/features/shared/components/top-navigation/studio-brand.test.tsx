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
      'Beta Disclaimer & Terms: This environment is experimental. Data created here may be reset, deleted, or not migrated to the launched version. Orcheo Cloud is currently offered as a free beta environment for evaluation and testing purposes only, provided on an "as-is" and "as-available" basis without warranties of any kind.',
    );
  });
});
