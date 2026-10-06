import { useCallback, useEffect, useState, type FormEvent } from "react";
import { KeyRound, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/design-system/ui/alert-dialog";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { toast } from "@/hooks/use-toast";
import { AuthApiError } from "@features/auth/lib/auth-api";
import {
  passkeyErrorMessage,
  passkeysSupported,
} from "@features/auth/lib/passkey-support";
import {
  deletePasskey,
  listPasskeys,
  registerPasskey,
  renamePasskey,
  syncAcceptedPasskeys,
  type PasskeyList,
  type PasskeySummary,
} from "@features/auth/lib/passkeys";
import { ConfirmIdentityDialog } from "./confirm-identity-dialog";

const formatDate = (value: string | null): string =>
  value ? new Date(value).toLocaleDateString() : "Never";

const failureToast = (title: string, error: unknown, fallback: string) => {
  const description = passkeyErrorMessage(error, fallback);
  if (description) {
    toast({ title, description, variant: "destructive" });
  }
};

interface PasskeysCardProps {
  /** The signed-in person's email, used to confirm it's them. */
  email: string | null;
}

export function PasskeysCard({ email }: PasskeysCardProps) {
  const supported = passkeysSupported();
  const [list, setList] = useState<PasskeyList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [renaming, setRenaming] = useState<PasskeySummary | null>(null);
  const [newName, setNewName] = useState("");
  const [removing, setRemoving] = useState<PasskeySummary | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async (): Promise<PasskeyList | null> => {
    try {
      const loaded = await listPasskeys();
      setList(loaded);
      setLoadError(null);
      return loaded;
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "Unable to load your passkeys.",
      );
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const addPasskey = async () => {
    setAdding(true);
    try {
      const passkey = await registerPasskey();
      toast({
        title: "Passkey added",
        description: `You can now sign in with "${passkey.name}".`,
      });
      await refresh();
    } catch (err) {
      if (
        err instanceof AuthApiError &&
        err.code === "auth.reauthentication_required"
      ) {
        if (email) {
          setConfirmOpen(true);
        } else {
          toast({
            title: "Sign in again to add a passkey",
            description: "Sign out, sign back in, then add your passkey.",
            variant: "destructive",
          });
        }
      } else {
        failureToast(
          "Couldn't add a passkey",
          err,
          "Your passkey could not be added.",
        );
      }
    } finally {
      setAdding(false);
    }
  };

  const openRename = (passkey: PasskeySummary) => {
    setRenaming(passkey);
    setNewName(passkey.name);
  };

  const saveName = async (event: FormEvent) => {
    event.preventDefault();
    if (!renaming) {
      return;
    }
    setSaving(true);
    try {
      await renamePasskey(renaming.id, newName.trim());
      setRenaming(null);
      await refresh();
    } catch (err) {
      failureToast(
        "Couldn't rename the passkey",
        err,
        "Unable to rename the passkey.",
      );
    } finally {
      setSaving(false);
    }
  };

  const confirmRemove = async () => {
    if (!removing) {
      return;
    }
    setSaving(true);
    try {
      await deletePasskey(removing.id);
      toast({ title: "Passkey removed" });
      const updated = await refresh();
      if (updated) {
        syncAcceptedPasskeys(updated);
      }
    } catch (err) {
      failureToast(
        "Couldn't remove the passkey",
        err,
        "Unable to remove the passkey.",
      );
    } finally {
      setSaving(false);
      setRemoving(null);
    }
  };

  const serverSupportsPasskeys = list?.rp_id !== null;
  const canAdd = supported && list !== null && serverSupportsPasskeys;

  return (
    <Card className="border-border/70 bg-muted/40 shadow-none">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1.5">
          <CardTitle>Passkeys</CardTitle>
          <CardDescription>
            Sign in with your fingerprint, face, or screen lock instead of an
            emailed code. You can always still sign in with your email.
          </CardDescription>
        </div>
        {canAdd && (
          <Button size="sm" onClick={() => void addPasskey()} disabled={adding}>
            {adding ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            Add a passkey
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {!supported && (
          <p className="text-sm text-muted-foreground">
            This browser can't use passkeys here. Passkeys need a recent browser
            and a secure (https) connection.
          </p>
        )}
        {list && !serverSupportsPasskeys && (
          <p className="text-sm text-muted-foreground">
            Passkeys aren't available on this Orcheo server.
          </p>
        )}
        {loadError && <p className="text-sm text-destructive">{loadError}</p>}
        {!list && !loadError && (
          <Loader2
            aria-label="Loading passkeys"
            className="h-5 w-5 animate-spin text-muted-foreground"
          />
        )}
        {list && list.passkeys.length === 0 && serverSupportsPasskeys && (
          <p className="text-sm text-muted-foreground">No passkeys yet.</p>
        )}
        {list && list.passkeys.length > 0 && (
          <ul className="divide-y divide-border rounded-md border border-border bg-background">
            {list.passkeys.map((passkey) => (
              <li
                key={passkey.id}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">
                        {passkey.name}
                      </span>
                      {passkey.backed_up && (
                        <Badge variant="secondary">Synced</Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Added {formatDate(passkey.created_at)} · Last used{" "}
                      {formatDate(passkey.last_used_at)}
                    </p>
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Rename ${passkey.name}`}
                    onClick={() => openRename(passkey)}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Remove ${passkey.name}`}
                    onClick={() => setRemoving(passkey)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {email && (
        <ConfirmIdentityDialog
          email={email}
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          onConfirmed={() => void addPasskey()}
        />
      )}

      <Dialog
        open={renaming !== null}
        onOpenChange={(open) => !open && setRenaming(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename passkey</DialogTitle>
            <DialogDescription>
              Pick a name that tells you where this passkey lives.
            </DialogDescription>
          </DialogHeader>
          <form
            id="rename-passkey-form"
            className="flex flex-col gap-2"
            onSubmit={saveName}
          >
            <Label htmlFor="passkey-name">Name</Label>
            <Input
              id="passkey-name"
              maxLength={64}
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
          </form>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenaming(null)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              form="rename-passkey-form"
              disabled={saving || !newName.trim()}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this passkey?</AlertDialogTitle>
            <AlertDialogDescription>
              You won't be able to sign in with "{removing?.name}" anymore. You
              can still sign in with a code sent to your email.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={saving}
              onClick={(event) => {
                event.preventDefault();
                void confirmRemove();
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
