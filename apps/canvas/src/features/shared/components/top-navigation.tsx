import React from "react";
import { Link } from "react-router-dom";
import { Bell } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { cn } from "@/lib/utils";
import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";
import VersionStatus from "@/features/shared/components/top-navigation/version-status";
import AccountMenu from "@/features/shared/components/top-navigation/account-menu";
import CanvasBrand from "@/features/shared/components/top-navigation/canvas-brand";
import type { TopNavigationProps } from "@/features/shared/components/top-navigation/top-navigation-types";

export default function TopNavigation({
  className,
  credentials = [],
  isCredentialsLoading = false,
  onAddCredential,
  onUpdateCredential,
  onDeleteCredential,
  onRevealCredentialSecret,
}: TopNavigationProps) {
  return (
    <header
      className={cn(
        "flex h-14 items-center border-b border-border bg-background px-4 lg:px-6",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3 lg:gap-6">
        <CanvasBrand />
        <ActiveWorkspaceIndicator />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <VersionStatus />
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
