import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
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
import { Label } from "@/design-system/ui/label";
import {
  startEmailChallenge,
  verifyEmailCode,
} from "@features/auth/lib/auth-api";

interface ConfirmIdentityDialogProps {
  email: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once a fresh sign-in has replaced the stored session. */
  onConfirmed: () => void;
}

/**
 * Re-verify the signed-in person with an emailed code. Sensitive actions such
 * as adding a passkey need a recent sign-in, and this provides one.
 */
export function ConfirmIdentityDialog({
  email,
  open,
  onOpenChange,
  onConfirmed,
}: ConfirmIdentityDialogProps) {
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setCodeSent(false);
      setCode("");
      setError(null);
    }
    onOpenChange(next);
  };

  const sendCode = async () => {
    setError(null);
    setBusy(true);
    try {
      await startEmailChallenge(email, "login");
      setCodeSent(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to send the sign-in email.",
      );
    } finally {
      setBusy(false);
    }
  };

  const verifyCode = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await verifyEmailCode(email, code.trim());
      handleOpenChange(false);
      onConfirmed();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "That code is invalid or expired.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Confirm it's you</DialogTitle>
          <DialogDescription>
            {codeSent
              ? `Enter the sign-in code we sent to ${email}.`
              : `To add a passkey, sign in again with a code sent to ${email}.`}
          </DialogDescription>
        </DialogHeader>
        {codeSent ? (
          <form
            id="confirm-identity-form"
            className="flex flex-col gap-2"
            onSubmit={verifyCode}
          >
            <Label htmlFor="confirm-identity-code">Sign-in code</Label>
            <Input
              id="confirm-identity-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              autoFocus
              placeholder="123456"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </form>
        ) : null}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          {codeSent ? (
            <Button
              type="submit"
              form="confirm-identity-form"
              disabled={busy || !code.trim()}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm"}
            </Button>
          ) : (
            <Button onClick={() => void sendCode()} disabled={busy}>
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Email me a code"
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
