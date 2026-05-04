import React from "react";
import { Link } from "react-router-dom";
import { Bell } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { cn } from "@/lib/utils";
import ProjectSwitcher from "@/features/shared/components/top-navigation/project-switcher";
import VersionStatus from "@/features/shared/components/top-navigation/version-status";
import WorkflowBreadcrumbs from "@/features/shared/components/top-navigation/workflow-breadcrumbs";
import CommandPaletteButton from "@/features/shared/components/top-navigation/command-palette-button";
import AccountMenu from "@/features/shared/components/top-navigation/account-menu";
import useWindowWidth from "@/features/shared/components/top-navigation/use-window-width";
import type { TopNavigationProps } from "@/features/shared/components/top-navigation/top-navigation-types";

export default function TopNavigation({
  currentWorkflow,
  className,
  credentials = [],
  isCredentialsLoading = false,
  onAddCredential,
  onUpdateCredential,
  onDeleteCredential,
  onRevealCredentialSecret,
}: TopNavigationProps) {
  const windowWidth = useWindowWidth();

  return (
    <header
      className={cn(
        "flex h-14 items-center border-b border-border bg-background px-4 lg:px-6",
        className,
      )}
    >
      <ProjectSwitcher />

      {currentWorkflow && (
        <WorkflowBreadcrumbs
          currentWorkflow={currentWorkflow}
          windowWidth={windowWidth}
        />
      )}

      <div className="ml-auto flex items-center gap-2">
        <VersionStatus />
        <CommandPaletteButton />
        <Button variant="outline" size="sm" asChild>
          <Link to="/workflow-remediations">Remediations</Link>
        </Button>
        <Button variant="ghost" size="icon">
          <Bell className="h-5 w-5" />
        </Button>
        <AccountMenu
          credentials={credentials}
          isCredentialsLoading={isCredentialsLoading}
          onAddCredential={onAddCredential}
          onUpdateCredential={onUpdateCredential}
          onDeleteCredential={onDeleteCredential}
          onRevealCredentialSecret={onRevealCredentialSecret}
        />
      </div>
    </header>
  );
}
