import { useCallback, useState } from "react";
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
import { Label } from "@/design-system/ui/label";
import { toast } from "@/hooks/use-toast";
import { updateWorkflowFromFiles } from "@features/workflow/lib/workflow-storage";
import { WorkflowFolderPicker } from "./workflow-folder-picker";
import type { WorkflowFolderSelection } from "@features/workflow/lib/workflow-folder-selection";

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
  const [scriptContent, setScriptContent] = useState<string | null>(null);
  const [scriptFileName, setScriptFileName] = useState("");
  const [configFileName, setConfigFileName] = useState<string | null>(null);
  const [configContent, setConfigContent] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setScriptContent(null);
    setScriptFileName("");
    setConfigFileName(null);
    setConfigContent(null);
    setIsUpdating(false);
    setError(null);
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

  const handleSelect = useCallback((selection: WorkflowFolderSelection) => {
    setError(null);
    setScriptContent(selection.scriptContent);
    setScriptFileName(selection.scriptName);
    setConfigFileName(selection.configName);
    setConfigContent(selection.configContent);
  }, []);

  const handleSelectionError = useCallback((message: string) => {
    setScriptContent(null);
    setScriptFileName("");
    setConfigFileName(null);
    setConfigContent(null);
    setError(message);
    toast({
      title: "Couldn't use that folder",
      description: message,
      variant: "destructive",
    });
  }, []);

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
        description: `${message} Fix the error and upload again.`,
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
            Select a folder with a new Python script and optional JSON config to
            create a new version of &ldquo;{workflowName}&rdquo;.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="update-folder">
              Workflow Folder <span className="text-destructive">*</span>
            </Label>
            <WorkflowFolderPicker
              idPrefix="update"
              scriptName={scriptFileName}
              configName={configFileName}
              onSelect={handleSelect}
              onError={handleSelectionError}
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
