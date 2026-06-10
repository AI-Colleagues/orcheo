import React, { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/design-system/ui/button";
import { cn } from "@/lib/utils";

import { useSidebarResize } from "./use-sidebar-resize";

export interface SidebarLayoutProps {
  /**
   * Sidebar content
   */
  sidebar: React.ReactNode;

  /**
   * Main content area
   */
  children: React.ReactNode;

  /**
   * Whether the sidebar is collapsed
   */
  isCollapsed?: boolean;

  /**
   * Callback when sidebar collapse state changes
   */
  onToggleCollapse?: () => void;

  /**
   * Sidebar width when expanded (default: 300px)
   */
  sidebarWidth?: number;

  /**
   * Sidebar width when collapsed (default: 50px)
   */
  collapsedWidth?: number;

  /**
   * Whether the sidebar is resizable
   */
  resizable?: boolean;

  /**
   * Minimum sidebar width when resizing (default: 200px)
   */
  minWidth?: number;

  /**
   * Maximum sidebar width when resizing (default: 500px)
   */
  maxWidth?: number;

  /**
   * Callback when sidebar width changes
   */
  onWidthChange?: (width: number) => void;

  /**
   * Whether to show collapse button
   */
  showCollapseButton?: boolean;

  /**
   * Sidebar position
   */
  position?: "left" | "right";

  /**
   * Additional CSS classes for the container
   */
  className?: string;

  /**
   * Additional CSS classes for the sidebar
   */
  sidebarClassName?: string;

  /**
   * Additional CSS classes for the main content
   */
  mainClassName?: string;

  /**
   * Whether the collapsed sidebar should overlay the content instead of
   * reserving layout width.
   */
  collapsedOverlay?: boolean;
}

/**
 * SidebarLayout - A reusable layout component with a collapsible, optionally
 * resizable sidebar and a main content area.
 *
 * This component provides:
 * - Collapsible sidebar with smooth transitions
 * - Optional resize handle for adjusting sidebar width
 * - Consistent styling across pages
 * - Support for left or right sidebar positioning
 * - Responsive behavior
 */
export default function SidebarLayout({
  sidebar,
  children,
  isCollapsed = false,
  onToggleCollapse,
  sidebarWidth = 300,
  collapsedWidth = 50,
  resizable = false,
  minWidth = 200,
  maxWidth = 500,
  onWidthChange,
  showCollapseButton = true,
  position = "left",
  className,
  sidebarClassName,
  mainClassName,
  collapsedOverlay = false,
}: SidebarLayoutProps) {
  const currentWidth = isCollapsed ? collapsedWidth : sidebarWidth;
  const renderedWidth = isCollapsed && collapsedOverlay ? 0 : currentWidth;
  const [isResizing, setIsResizing] = useState(false);
  const leftAsideRef = useRef<HTMLElement | null>(null);
  const rightAsideRef = useRef<HTMLElement | null>(null);

  const setSidebarWidthVariable = (
    aside: HTMLElement | null,
    width: number,
  ): void => {
    aside?.style.setProperty("--sidebar-layout-width", `${width}px`);
  };

  useEffect(() => {
    setSidebarWidthVariable(leftAsideRef.current, renderedWidth);
    setSidebarWidthVariable(rightAsideRef.current, renderedWidth);
  }, [renderedWidth]);

  const { handleMouseDown } = useSidebarResize({
    resizable,
    isCollapsed,
    sidebarWidth,
    minWidth,
    maxWidth,
    position,
    onWidthChange,
    onLiveWidthChange: (width) => {
      if (position === "left") {
        setSidebarWidthVariable(leftAsideRef.current, width);
        return;
      }
      setSidebarWidthVariable(rightAsideRef.current, width);
    },
    onResizeStart: () => {
      setIsResizing(true);
      // Immediately disable the CSS transition via direct DOM mutation so the
      // sidebar edge snaps to the cursor on the very first RAF tick, before the
      // React re-render that removes the transition class can fire.
      const aside =
        position === "left" ? leftAsideRef.current : rightAsideRef.current;
      aside?.style.setProperty("transition", "none");
    },
    onResizeEnd: () => {
      setIsResizing(false);
      const aside =
        position === "left" ? leftAsideRef.current : rightAsideRef.current;
      aside?.style.removeProperty("transition");
    },
  });

  return (
    <div className={cn("flex h-full min-h-0 overflow-hidden", className)}>
      {position === "left" && (
        <>
          <aside
            ref={leftAsideRef}
            className={cn(
              "relative flex h-full shrink-0 flex-col",
              isCollapsed && collapsedOverlay
                ? "overflow-visible border-0 bg-transparent"
                : "border-r border-border bg-card",
              !isResizing && "transition-[width] duration-300",
              sidebarClassName,
            )}
            style={{
              width: "var(--sidebar-layout-width)",
              willChange: isResizing ? "width" : undefined,
            }}
          >
            {sidebar}

            {isResizing && <div className="absolute inset-0 z-10" />}

            {showCollapseButton && onToggleCollapse && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onToggleCollapse}
                className="absolute top-3 right-3 z-10"
                title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              >
                {isCollapsed ? (
                  <ChevronRight className="h-5 w-5" />
                ) : (
                  <ChevronLeft className="h-5 w-5" />
                )}
              </Button>
            )}

            {resizable && !isCollapsed && (
              <div
                data-testid="sidebar-resize-handle"
                className="absolute top-0 right-0 bottom-0 w-2 cursor-col-resize hover:bg-primary/20 transition-colors"
                onMouseDown={handleMouseDown}
              />
            )}
          </aside>
          <main
            className={cn(
              "flex-1 h-full min-h-0 min-w-0 overflow-hidden",
              isResizing && "pointer-events-none",
              mainClassName,
            )}
          >
            {children}
          </main>
        </>
      )}

      {position === "right" && (
        <>
          <main
            className={cn(
              "flex-1 h-full min-h-0 min-w-0 overflow-hidden",
              isResizing && "pointer-events-none",
              mainClassName,
            )}
          >
            {children}
          </main>
          <aside
            ref={rightAsideRef}
            className={cn(
              "relative flex h-full shrink-0 flex-col",
              isCollapsed && collapsedOverlay
                ? "overflow-visible border-0 bg-transparent"
                : "border-l border-border bg-card",
              !isResizing && "transition-[width] duration-300",
              sidebarClassName,
            )}
            style={{
              width: "var(--sidebar-layout-width)",
              willChange: isResizing ? "width" : undefined,
            }}
          >
            {sidebar}

            {isResizing && <div className="absolute inset-0 z-10" />}

            {showCollapseButton && onToggleCollapse && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onToggleCollapse}
                className="absolute top-3 left-3 z-10"
                title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              >
                {isCollapsed ? (
                  <ChevronLeft className="h-5 w-5" />
                ) : (
                  <ChevronRight className="h-5 w-5" />
                )}
              </Button>
            )}

            {resizable && !isCollapsed && (
              <div
                data-testid="sidebar-resize-handle"
                className="absolute top-0 left-0 bottom-0 w-2 cursor-col-resize hover:bg-primary/20 transition-colors"
                onMouseDown={handleMouseDown}
              />
            )}
          </aside>
        </>
      )}
    </div>
  );
}
