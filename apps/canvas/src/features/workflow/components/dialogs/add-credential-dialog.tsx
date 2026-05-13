import React, { useCallback, useMemo, useState } from "react";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/design-system/ui/dialog";
import { Input } from "@/design-system/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import { Loader2, Plus } from "lucide-react";
import type {
  CredentialInput,
  CredentialVaultAccessLevel,
} from "@features/workflow/types/credential-vault";

type CredentialAccess = CredentialVaultAccessLevel;

interface AddCredentialDialogProps {
  onAddCredential?: (credential: CredentialInput) => Promise<void> | void;
}

const DEFAULT_CREDENTIAL: CredentialInput = {
  name: "",
  provider: "custom",
  access: "shared",
  secrets: { secret: "" },
};

export function AddCredentialDialog({
  onAddCredential,
}: AddCredentialDialogProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [pendingCredential, setPendingCredential] =
    useState<CredentialInput>(DEFAULT_CREDENTIAL);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasName = pendingCredential.name.trim().length > 0;
  const hasProvider = pendingCredential.provider.trim().length > 0;

  const resetState = useCallback(() => {
    setPendingCredential(DEFAULT_CREDENTIAL);
    setError(null);
    setIsSaving(false);
  }, []);

  const handleOpenChange = useCallback(
    (open: boolean) => {
      setIsOpen(open);
      if (!open) {
        resetState();
      }
    },
    [resetState],
  );

  const handleAddCredential = useCallback(async () => {
    if (!onAddCredential || !hasName) {
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await onAddCredential(pendingCredential);
      handleOpenChange(false);
    } catch (err) {
      console.error("Failed to save credential", err);
      const message =
        err instanceof Error
          ? err.message
          : "Unable to save credential. Please try again.";
      setError(message);
    } finally {
      setIsSaving(false);
    }
  }, [handleOpenChange, hasName, onAddCredential, pendingCredential]);

  const updateCredential = useCallback((partial: Partial<CredentialInput>) => {
    setPendingCredential((prev) => ({
      ...prev,
      ...partial,
      secrets: partial.secrets ?? prev.secrets,
    }));
  }, []);

  const updateSecret = useCallback(
    (key: string, value: string) => {
      updateCredential({
        secrets: {
          ...pendingCredential.secrets,
          [key]: value,
        },
      });
    },
    [pendingCredential.secrets, updateCredential],
  );

  const isSaveDisabled = useMemo(
    () => !hasName || !hasProvider || isSaving,
    [hasName, hasProvider, isSaving],
  );

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4 mr-2" />
          Add Credential
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Add New Credential</DialogTitle>
          <DialogDescription>
            Create a new credential for connecting to external services.
            Credentials are encrypted at rest.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="credential-name"
              className="text-right text-sm font-medium"
            >
              Name
            </label>
            <Input
              id="credential-name"
              value={pendingCredential.name}
              onChange={(event) =>
                updateCredential({ name: event.target.value })
              }
              className="col-span-3"
              placeholder="My API Credential"
            />
          </div>
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="credential-provider"
              className="text-right text-sm font-medium"
            >
              Provider
            </label>
            <Input
              id="credential-provider"
              value={pendingCredential.provider}
              onChange={(event) =>
                updateCredential({ provider: event.target.value })
              }
              className="col-span-3"
              placeholder="openai, pinecone, mongodb..."
            />
          </div>
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="credential-access"
              className="text-right text-sm font-medium"
            >
              Access
            </label>
            <Select
              value={pendingCredential.access}
              onValueChange={(value: CredentialAccess) =>
                updateCredential({ access: value })
              }
            >
              <SelectTrigger id="credential-access" className="col-span-3">
                <SelectValue placeholder="Select access level" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="scoped">Scoped</SelectItem>
                <SelectItem value="shared">Shared</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="credential-secret"
              className="text-right text-sm font-medium"
            >
              Secret
            </label>
            <Input
              id="credential-secret"
              type="password"
              value={pendingCredential.secrets?.secret ?? ""}
              onChange={(event) => updateSecret("secret", event.target.value)}
              className="col-span-3"
              placeholder="Enter credential secret"
            />
          </div>
        </div>
        {error ? (
          <p className="text-sm text-destructive px-1">{error}</p>
        ) : null}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isSaving}
          >
            Cancel
          </Button>
          <Button onClick={handleAddCredential} disabled={isSaveDisabled}>
            {isSaving ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save Credential"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
