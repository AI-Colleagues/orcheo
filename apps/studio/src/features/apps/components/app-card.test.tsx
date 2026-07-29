import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { HostedApp } from "../data/sample-apps";
import { AppCard } from "./app-card";

const app: HostedApp = {
  id: "app-1",
  name: "Portal",
  alias: "portal",
  url: "http://portal.apps.localhost:2030/",
  visibility: "public",
  state: "draft",
  health: "unknown",
  updated: "just now",
  deployments: [],
  bindings: [],
  collections: [],
  permissionRevision: 1,
};

describe("AppCard", () => {
  it("starts the archive flow from its Delete action", async () => {
    const user = userEvent.setup();
    const onArchiveApp = vi.fn();

    render(
      <AppCard
        app={app}
        onOpen={vi.fn()}
        onTogglePublish={vi.fn()}
        onArchiveApp={onArchiveApp}
      />,
    );

    await user.click(screen.getByRole("button", { name: "App actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));

    expect(onArchiveApp).toHaveBeenCalledWith(app);
  });
});
