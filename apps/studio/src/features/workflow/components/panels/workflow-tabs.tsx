import { useState } from "react";
import { Vault } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/design-system/ui/tabs";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/design-system/ui/dialog";
import CredentialsVault from "@features/workflow/components/dialogs/credentials-vault";
import type {
  Credential,
  CredentialInput,
  CredentialUpdateInput,
} from "@features/workflow/types/credential-vault";

interface WorkflowTabsProps {
  activeTab: string;
  onTabChange: (value: string) => void;
  currentWorkflow?: {
    name: string;
    onNameChange?: (name: string) => void;
  };
  credentials?: Credential[];
  isCredentialsLoading?: boolean;
  onAddCredential?: (credential: CredentialInput) => Promise<void> | void;
  onUpdateCredential?: (
    id: string,
    updates: CredentialUpdateInput,
  ) => Promise<void> | void;
  onDeleteCredential?: (id: string) => Promise<void> | void;
  onRevealCredentialSecret?: (id: string) => Promise<string | null>;
}

export default function WorkflowTabs({
  activeTab,
  onTabChange,
  currentWorkflow,
  credentials = [],
  isCredentialsLoading = false,
  onAddCredential,
  onUpdateCredential,
  onDeleteCredential,
  onRevealCredentialSecret,
}: WorkflowTabsProps) {
  const [isVaultOpen, setIsVaultOpen] = useState(false);

  return (
    <div className="flex items-center gap-3 border-b border-border px-3">
      {currentWorkflow && (
        <span className="min-w-0 truncate text-sm font-medium text-foreground">
          {currentWorkflow.name}
        </span>
      )}
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-fit">
        <TabsList className="h-9">
          <TabsTrigger value="workflow" className="gap-1.5 text-sm px-3 py-1.5">
            Workflow
          </TabsTrigger>
          <TabsTrigger value="trace" className="gap-1.5 text-sm px-3 py-1.5">
            Trace
          </TabsTrigger>
          <TabsTrigger value="settings" className="gap-1.5 text-sm px-3 py-1.5">
            Settings
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="ml-auto flex items-center">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Credential Vault"
          title="Credential Vault"
          onClick={() => setIsVaultOpen(true)}
        >
          <Vault className="h-4 w-4" />
        </Button>
      </div>

      <Dialog open={isVaultOpen} onOpenChange={setIsVaultOpen}>
        <DialogContent className="max-h-[85vh] max-w-[67.2rem] overflow-hidden">
          <DialogTitle className="sr-only">Credential Vault</DialogTitle>
          <DialogDescription className="sr-only">
            Manage, search, add, and remove credentials scoped to this
            workflow.
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
