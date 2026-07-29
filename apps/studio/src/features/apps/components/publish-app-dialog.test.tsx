import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PublishAppDialog } from "./publish-app-dialog";

describe("PublishAppDialog", () => {
  it("defaults the selection to the app's current visibility", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <PublishAppDialog
        open
        currentVisibility="private"
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(
      screen.getByRole("button", { name: /workspace only/i }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^public/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(onConfirm).toHaveBeenCalledWith("private");
  });

  it("falls back to public when no current visibility is known", () => {
    render(
      <PublishAppDialog
        open
        onOpenChange={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /^public/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
