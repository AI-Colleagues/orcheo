import React, { useMemo, useState } from "react";
import { Input } from "@/design-system/ui/input";
import { Search } from "lucide-react";
import type {
  Credential,
  CredentialInput,
  CredentialUpdateInput,
} from "@features/workflow/types/credential-vault";
import { AddCredentialDialog } from "./add-credential-dialog";
import { CredentialsTable } from "./credentials-table";

interface CredentialsVaultProps {
  credentials?: Credential[];
  isLoading?: boolean;
  onAddCredential?: (credential: CredentialInput) => Promise<void> | void;
  onUpdateCredential?: (
    id: string,
    updates: CredentialUpdateInput,
  ) => Promise<void> | void;
  onDeleteCredential?: (id: string) => Promise<void> | void;
  onRevealCredentialSecret?: (id: string) => Promise<string | null>;
  className?: string;
}

export default function CredentialsVault({
  credentials = [],
  isLoading = false,
  onAddCredential,
  onUpdateCredential,
  onDeleteCredential,
  onRevealCredentialSecret,
  className,
}: CredentialsVaultProps) {
  const [searchQuery, setSearchQuery] = useState("");

  const containerClassName = useMemo(
    () =>
      ["flex min-h-0 min-w-0 max-h-[75vh] flex-col gap-4", className]
        .filter((value): value is string => Boolean(value && value.trim()))
        .join(" "),
    [className],
  );

  return (
    <div className={containerClassName}>
      <div className="flex flex-wrap items-start justify-between gap-3 pr-10">
        <div className="space-y-1">
          <h2 className="text-xl font-bold">Credential Vault</h2>
          <p className="text-sm text-muted-foreground">
            Credentials stored here are securely injected into your workflows at
            runtime, so nodes can authenticate to external services without
            hardcoding secrets.
          </p>
        </div>
        <AddCredentialDialog onAddCredential={onAddCredential} />
      </div>

      <div className="flex min-w-0 items-center">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search credentials..."
            className="pl-8"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </div>
      </div>

      <div
        className="min-h-0 flex-1 overflow-y-auto pr-1"
        data-testid="credentials-vault-list"
      >
        <CredentialsTable
          credentials={credentials}
          isLoading={isLoading}
          searchQuery={searchQuery}
          onUpdateCredential={onUpdateCredential}
          onDeleteCredential={onDeleteCredential}
          onRevealCredentialSecret={onRevealCredentialSecret}
        />
      </div>
    </div>
  );
}
