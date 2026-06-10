import { cn } from "@/lib/utils";
import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";
import VersionStatus from "@/features/shared/components/top-navigation/version-status";
import AccountMenu from "@/features/shared/components/top-navigation/account-menu";
import StudioBrand from "@/features/shared/components/top-navigation/studio-brand";
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
        "flex h-14 shrink-0 items-center border-b border-border bg-background px-4 lg:px-6",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3 lg:gap-6">
        <StudioBrand />
        <ActiveWorkspaceIndicator />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <VersionStatus />
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
