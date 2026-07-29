import { useEffect, useState } from "react";
import { Check, Globe, Lock } from "lucide-react";

import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { cn } from "@/lib/utils";
import type { AppVisibility } from "../data/sample-apps";

interface PublishOption {
  value: AppVisibility;
  title: string;
  description: string;
  icon: typeof Globe;
}

const PUBLISH_OPTIONS: PublishOption[] = [
  {
    value: "public",
    title: "Public",
    description: "Anyone with the link can open and use the app.",
    icon: Globe,
  },
  {
    value: "private",
    title: "Workspace only",
    description: "Only signed-in members of this workspace can use the app.",
    icon: Lock,
  },
];

interface PublishAppDialogProps {
  open: boolean;
  currentVisibility?: AppVisibility;
  isPending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (visibility: AppVisibility) => Promise<void> | void;
}

export function PublishAppDialog({
  open,
  currentVisibility,
  isPending = false,
  onOpenChange,
  onConfirm,
}: PublishAppDialogProps) {
  const [selected, setSelected] = useState<AppVisibility>(
    currentVisibility ?? "public",
  );

  useEffect(() => {
    if (open) {
      setSelected(currentVisibility ?? "public");
    }
  }, [open, currentVisibility]);

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Publish app</DialogTitle>
          <DialogDescription>
            Choose who can access the published app.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 py-2">
          {PUBLISH_OPTIONS.map((option) => {
            const Icon = option.icon;
            const isSelected = selected === option.value;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={isSelected}
                onClick={() => setSelected(option.value)}
                disabled={isPending}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3 text-left transition-colors",
                  isSelected
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-muted/40",
                )}
              >
                <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{option.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {option.description}
                  </p>
                </div>
                {isSelected ? (
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                ) : null}
              </button>
            );
          })}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void onConfirm(selected)}
            disabled={isPending}
          >
            {isPending ? "Publishing…" : "Publish"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
