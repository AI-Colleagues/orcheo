import { useEffect, useState } from "react";
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

interface CreateAppDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (name: string, alias: string) => void;
}

const toSlug = (name: string): string =>
  name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");

export function CreateAppDialog({
  open,
  onOpenChange,
  onCreate,
}: CreateAppDialogProps) {
  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [aliasDirty, setAliasDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setName("");
      setAlias("");
      setAliasDirty(false);
      setError(null);
    }
  }, [open]);

  const handleNameChange = (value: string) => {
    setName(value);
    if (!aliasDirty) {
      setAlias(toSlug(value));
    }
  };

  const handleSubmit = () => {
    const trimmedName = name.trim();
    const trimmedAlias = alias.trim() || toSlug(name);
    if (!trimmedName || !trimmedAlias) {
      setError("Both name and alias are required.");
      return;
    }
    try {
      onCreate(trimmedName, trimmedAlias);
      onOpenChange(false);
    } catch (createError) {
      setError(
        createError instanceof Error
          ? createError.message
          : "Unable to create the app.",
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create app</DialogTitle>
          <DialogDescription>
            Reserves an alias for a hosted web app. Upload a deployment once
            it's ready to publish.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="app-name">Name</Label>
            <Input
              id="app-name"
              placeholder="e.g. Research Digest"
              value={name}
              onChange={(event) => handleNameChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleSubmit();
              }}
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="app-alias">Alias</Label>
            <Input
              id="app-alias"
              placeholder="e.g. research-digest"
              value={alias}
              onChange={(event) => {
                setAliasDirty(true);
                setAlias(toSlug(event.target.value));
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleSubmit();
              }}
            />
            <p className="text-xs text-muted-foreground">
              Your app will be reachable at this alias once published.
            </p>
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit}>Create app</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
