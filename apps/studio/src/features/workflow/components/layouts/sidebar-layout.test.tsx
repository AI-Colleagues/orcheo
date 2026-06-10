import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import SidebarLayout from "./sidebar-layout";

const getSidebarWidthVariable = (sidebar: HTMLElement) =>
  sidebar.style.getPropertyValue("--sidebar-layout-width");

describe("SidebarLayout", () => {
  afterEach(() => {
    cleanup();
  });

  it("commits the resized width once on pointer up", () => {
    const handleWidthChange = vi.fn();

    render(
      <SidebarLayout
        sidebar={<div>sidebar</div>}
        sidebarWidth={320}
        resizable
        onWidthChange={handleWidthChange}
      >
        <div>content</div>
      </SidebarLayout>,
    );

    const resizeHandle = screen.getByTestId("sidebar-resize-handle");
    const sidebar = screen.getByText("sidebar").closest("aside");

    if (!sidebar) {
      throw new Error("Sidebar aside was not rendered");
    }

    expect(getSidebarWidthVariable(sidebar)).toBe("320px");

    fireEvent.mouseDown(resizeHandle, { clientX: 320 });
    fireEvent.mouseMove(document, { clientX: 420, buttons: 1 });

    expect(handleWidthChange).not.toHaveBeenCalled();

    fireEvent.mouseUp(document, { clientX: 420 });

    expect(handleWidthChange).toHaveBeenCalledTimes(1);
    expect(handleWidthChange).toHaveBeenCalledWith(420);
  });

  it("ignores non-left-button resize attempts", () => {
    const handleWidthChange = vi.fn();

    render(
      <SidebarLayout
        sidebar={<div>sidebar</div>}
        sidebarWidth={320}
        resizable
        onWidthChange={handleWidthChange}
      >
        <div>content</div>
      </SidebarLayout>,
    );

    const resizeHandle = screen.getByTestId("sidebar-resize-handle");

    fireEvent.mouseDown(resizeHandle, { clientX: 320, button: 2 });
    fireEvent.mouseMove(document, { clientX: 420, buttons: 0 });
    fireEvent.mouseUp(document, { clientX: 420 });

    expect(handleWidthChange).not.toHaveBeenCalled();
  });

  it("stops resizing once the left mouse button is released", () => {
    const handleWidthChange = vi.fn();

    render(
      <SidebarLayout
        sidebar={<div>sidebar</div>}
        sidebarWidth={320}
        resizable
        onWidthChange={handleWidthChange}
      >
        <div>content</div>
      </SidebarLayout>,
    );

    const resizeHandle = screen.getByTestId("sidebar-resize-handle");

    fireEvent.mouseDown(resizeHandle, { clientX: 320, button: 0 });
    fireEvent.mouseMove(document, { clientX: 380, buttons: 1 });
    fireEvent.mouseMove(document, { clientX: 420, buttons: 0 });
    fireEvent.mouseMove(document, { clientX: 460, buttons: 0 });

    expect(handleWidthChange).toHaveBeenCalledTimes(1);
    expect(handleWidthChange).toHaveBeenCalledWith(380);
  });

  it("collapses into an overlay without reserving layout width", () => {
    render(
      <SidebarLayout
        sidebar={<button type="button">sidebar</button>}
        isCollapsed
        collapsedOverlay
      >
        <div>content</div>
      </SidebarLayout>,
    );

    const sidebar = screen
      .getByRole("button", { name: "sidebar" })
      .closest("aside");

    if (!sidebar) {
      throw new Error("Sidebar aside was not rendered");
    }

    expect(getSidebarWidthVariable(sidebar)).toBe("0px");
    expect(sidebar.className).toContain("bg-transparent");
    expect(sidebar.className).toContain("border-0");
  });
});
