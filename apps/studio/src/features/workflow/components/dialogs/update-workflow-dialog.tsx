import { useCallback, useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
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
import { toast } from "@/hooks/use-toast";
import { updateWorkflowFromFiles } from "@features/workflow/lib/workflow-storage";

interface UpdateWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  workflowName: string;
}

export function UpdateWorkflowDialog({
  open,
  onOpenChange,
  workflowId,
  workflowName,
}: UpdateWorkflowDialogProps) {
  const scriptInputRef = useRef<HTMLInputElement>(null);
  const configInputRef = useRef<HTMLInputElement>(null);

  const [scriptContent, setScriptContent] = useState<string | null>(null);
  const [scriptFileName, setScriptFileName] = useState("");
  const [configContent, setConfigContent] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setScriptContent(null);
    setScriptFileName("");
    setConfigContent(null);
    setIsUpdating(false);
    setError(null);
    if (scriptInputRef.current) {
      scriptInputRef.current.value = "";
    }
    if (configInputRef.current) {
      configInputRef.current.value = "";
    }
  }, []);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        reset();
      }
      onOpenChange(nextOpen);
    },
    [onOpenChange, reset],
  );

  const handleScriptFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }
      setScriptFileName(file.name);
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text === "string") {
          setScriptContent(text);
        }
      };
      reader.readAsText(file);
    },
    [],
  );

  const handleConfigFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text !== "string") {
          return;
        }
        try {
          const parsed = JSON.parse(text) as Record<string, unknown>;
          setConfigContent(parsed);
          setError(null);
        } catch {
          setError("config.json is not valid JSON.");
          setConfigContent(null);
        }
      };
      reader.readAsText(file);
    },
    [],
  );

  const handleUpdate = useCallback(async () => {
    if (!scriptContent) {
      return;
    }
    setIsUpdating(true);
    setError(null);
    try {
      await updateWorkflowFromFiles(workflowId, scriptContent, configContent);
      toast({
        title: "Workflow updated",
        description: `"${workflowName}" has been updated with a new version.`,
      });
      handleOpenChange(false);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Update failed. Please try again.";
      setError(message);
      toast({
        title: "Update failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setIsUpdating(false);
    }
  }, [
    scriptContent,
    configContent,
    workflowId,
    workflowName,
    handleOpenChange,
  ]);

  const canUpdate = Boolean(scriptContent);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Update Workflow</DialogTitle>
          <DialogDescription>
            Upload a new Python script and optional JSON config to create a new
            version of &ldquo;{workflowName}&rdquo;.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="update-script">
              Workflow Script <span className="text-destructive">*</span>
            </Label>
            <Input
              id="update-script"
              ref={scriptInputRef}
              type="file"
              accept=".py"
              onChange={handleScriptFileChange}
              disabled={isUpdating}
            />
            {scriptFileName && (
              <p className="text-xs text-muted-foreground">{scriptFileName}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="update-config">Config (optional)</Label>
            <Input
              id="update-config"
              ref={configInputRef}
              type="file"
              accept=".json"
              onChange={handleConfigFileChange}
              disabled={isUpdating}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isUpdating}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleUpdate()}
            disabled={!canUpdate || isUpdating}
          >
            {isUpdating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Updating...
              </>
            ) : (
              <>
                <RefreshCw className="mr-2 h-4 w-4" />
                Update
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
