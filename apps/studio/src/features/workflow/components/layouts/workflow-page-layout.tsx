import React from "react";
import { cn } from "@/lib/utils";

export interface WorkflowPageLayoutProps {
  /**
   * Header content (e.g., navigation, title, actions)
   */
  header?: React.ReactNode;

  /**
   * Main content area
   */
  children: React.ReactNode;

  /**
   * Additional CSS classes for the container
   */
  className?: string;

  /**
   * Additional CSS classes for the header
   */
  headerClassName?: string;

  /**
   * Additional CSS classes for the main content
   */
  mainClassName?: string;
}

/**
 * WorkflowPageLayout - A reusable page layout component for workflow pages.
 *
 * This component provides:
 * - Consistent full-height layout
 * - Optional header area
 * - Main content area that fills remaining space
 * - Proper overflow handling
 */
export default function WorkflowPageLayout({
  header,
  children,
  className,
  headerClassName,
  mainClassName,
}: WorkflowPageLayoutProps) {
  return (
    <div
      className={cn(
        "h-screen w-full flex flex-col overflow-hidden bg-background",
        className,
      )}
    >
      {header && (
        <header
          className={cn(
            "flex-shrink-0 border-b border-border bg-card",
            headerClassName,
          )}
        >
          {header}
        </header>
      )}
      <main className={cn("flex-1 overflow-hidden min-h-0", mainClassName)}>
        {children}
      </main>
    </div>
  );
}
