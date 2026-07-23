import { useState, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/design-system/ui/dialog";
import CredentialsVault from "@features/workflow/components/dialogs/credentials-vault";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import {
  getSidebarCollapsed,
  setSidebarCollapsed,
} from "@/lib/sidebar-session";
import { cn } from "@/lib/utils";
import AppSidebar from "./app-sidebar";

const SIDEBAR_WIDTH_CLASS = "w-60";

interface AppShellProps {
  children: ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(getSidebarCollapsed);
  const [peek, setPeek] = useState(false);
  const [isVaultOpen, setIsVaultOpen] = useState(false);
  const { setVaultOpen } = usePageContext();

  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault();

  const handleToggleCollapsed = () => {
    setCollapsed((previous) => {
      const next = !previous;
      setSidebarCollapsed(next);
      return next;
    });
    setPeek(false);
  };

  const handleVaultOpenChange = (open: boolean) => {
    setIsVaultOpen(open);
    setVaultOpen(open);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {collapsed && (
        <div
          className="fixed inset-y-0 left-0 z-40 w-3.5"
          onMouseEnter={() => setPeek(true)}
        />
      )}
      <aside
        className={cn(
          "flex h-full flex-col bg-card transition-transform duration-300 ease-out",
          SIDEBAR_WIDTH_CLASS,
          collapsed
            ? "fixed inset-y-0 left-0 z-40 border-r border-border shadow-xl"
            : "relative shrink-0 border-r border-border",
          collapsed && !peek && "-translate-x-full",
        )}
        onMouseEnter={() => collapsed && setPeek(true)}
        onMouseLeave={() => setPeek(false)}
      >
        <AppSidebar
          collapsed={collapsed}
          onToggleCollapsed={handleToggleCollapsed}
          onOpenVault={() => handleVaultOpenChange(true)}
        />
      </aside>
      <main className="min-w-0 flex-1 overflow-hidden">{children}</main>

      <Dialog open={isVaultOpen} onOpenChange={handleVaultOpenChange}>
        <DialogContent className="max-h-[85vh] max-w-[67.2rem] overflow-hidden">
          <DialogTitle className="sr-only">Credential Vault</DialogTitle>
          <DialogDescription className="sr-only">
            Manage, search, add, and remove credentials.
          </DialogDescription>
          <CredentialsVault
            credentials={credentials}
            isLoading={isCredentialsLoading}
            onAddCredential={onAddCredential}
            onUpdateCredential={onUpdateCredential}
            onDeleteCredential={onDeleteCredential}
            onRevealCredentialSecret={onRevealCredentialSecret}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
