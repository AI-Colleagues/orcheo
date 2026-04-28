import React, { useEffect, useMemo, useState } from "react";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { Input } from "@/design-system/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import type {
  Credential,
  CredentialUpdateInput,
  CredentialVaultAccessLevel,
} from "@features/workflow/types/credential-vault";

type CredentialAccess = CredentialVaultAccessLevel;

interface EditCredentialDialogProps {
  credential: Credential | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUpdateCredential?: (
    id: string,
    updates: CredentialUpdateInput,
  ) => Promise<void> | void;
}

export function EditCredentialDialog({
  credential,
  open,
  onOpenChange,
  onUpdateCredential,
}: EditCredentialDialogProps) {
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [access, setAccess] = useState<CredentialAccess>("scoped");
  const [secret, setSecret] = useState("");
  const [initialSecret, setInitialSecret] = useState("");
  const [isSecretVisible, setIsSecretVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!credential || !open) {
      return;
    }

    const credentialSecret = credential.secrets
      ? (credential.secrets.secret ??
        Object.values(credential.secrets)[0] ??
        "")
      : "";

    setName(credential.name);
    setProvider(credential.provider ?? credential.type ?? "");
    setAccess(credential.access);
    setSecret(credentialSecret);
    setInitialSecret(credentialSecret);
    setIsSecretVisible(false);
    setError(null);
    setIsSaving(false);
  }, [credential, open]);

  const handleSave = async () => {
    if (!credential || !onUpdateCredential) {
      return;
    }

    const trimmedName = name.trim();
    const trimmedProvider = provider.trim();
    const trimmedSecret = secret.trim();
    const trimmedInitialSecret = initialSecret.trim();
    if (!trimmedName) {
      setError("Credential name is required.");
      return;
    }
    if (!trimmedProvider) {
      setError("Provider is required.");
      return;
    }

    const updates: CredentialUpdateInput = {};
    if (trimmedName !== credential.name) {
      updates.name = trimmedName;
    }
    const existingProvider = (
      credential.provider ??
      credential.type ??
      ""
    ).trim();
    if (trimmedProvider !== existingProvider) {
      updates.provider = trimmedProvider;
    }
    if (access !== credential.access) {
      updates.access = access;
    }
    if (trimmedSecret.length > 0 && trimmedSecret !== trimmedInitialSecret) {
      updates.secrets = { secret: trimmedSecret };
    }

    if (Object.keys(updates).length === 0) {
      setError("No changes detected.");
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await onUpdateCredential(credential.id, updates);
      onOpenChange(false);
    } catch (updateError) {
      setError(
        updateError instanceof Error
          ? updateError.message
          : "Unable to update credential.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const isSaveDisabled = useMemo(
    () => !credential || isSaving,
    [credential, isSaving],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Edit Credential</DialogTitle>
          <DialogDescription>
            Update credential metadata or rotate the secret value.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="edit-credential-name"
              className="text-right text-sm font-medium"
            >
              Name
            </label>
            <Input
              id="edit-credential-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="col-span-3"
            />
          </div>
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="edit-credential-provider"
              className="text-right text-sm font-medium"
            >
              Provider
            </label>
            <Input
              id="edit-credential-provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              className="col-span-3"
            />
          </div>
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="edit-credential-access"
              className="text-right text-sm font-medium"
            >
              Access
            </label>
            <Select
              value={access}
              onValueChange={(value: CredentialAccess) => setAccess(value)}
            >
              <SelectTrigger id="edit-credential-access" className="col-span-3">
                <SelectValue placeholder="Select access level" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="scoped">Scoped</SelectItem>
                <SelectItem value="shared">Shared</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {access === "scoped" && credential?.workflowId ? (
            <div className="grid grid-cols-4 items-center gap-4">
              <span className="text-right text-sm font-medium">
                Workflow ID
              </span>
              <code
                className="col-span-3 truncate rounded bg-muted px-2 py-1 text-xs font-mono"
                title={credential.workflowId}
                data-testid="edit-credential-workflow-id"
              >
                {credential.workflowId}
              </code>
            </div>
          ) : null}
          <div className="grid grid-cols-4 items-center gap-4">
            <label
              htmlFor="edit-credential-secret"
              className="text-right text-sm font-medium"
            >
              Secret
            </label>
            <div className="col-span-3 flex items-center gap-2">
              <Input
                id="edit-credential-secret"
                type={isSecretVisible ? "text" : "password"}
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                className="flex-1"
                placeholder="Enter a secret value"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => setIsSecretVisible((previous) => !previous)}
                disabled={!secret}
                aria-label={`${isSecretVisible ? "Hide" : "Show"} secret value`}
              >
                {isSecretVisible ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>
        </div>
        {error ? (
          <p className="px-1 text-sm text-destructive">{error}</p>
        ) : null}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isSaving}
          >
            Cancel
          </Button>
          <Button onClick={() => void handleSave()} disabled={isSaveDisabled}>
            {isSaving ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save Changes"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
