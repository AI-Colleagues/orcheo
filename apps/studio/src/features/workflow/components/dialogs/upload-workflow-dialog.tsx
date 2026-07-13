import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, Upload } from "lucide-react";
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
import {
  uploadWorkflowFromFiles,
  WorkflowUploadFailedError,
} from "@features/workflow/lib/workflow-storage";
import { parseWorkflowFrontmatter } from "@features/workflow/lib/workflow-frontmatter";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import { getWorkspaceWorkflowPath } from "@/lib/workspace-routing";
import { WorkflowFolderPicker } from "./workflow-folder-picker";
import type { WorkflowFolderSelection } from "@features/workflow/lib/workflow-folder-selection";

interface UploadWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UploadWorkflowDialog({
  open,
  onOpenChange,
}: UploadWorkflowDialogProps) {
  const navigate = useNavigate();

  const [workflowName, setWorkflowName] = useState("");
  const [scriptContent, setScriptContent] = useState<string | null>(null);
  const [scriptFileName, setScriptFileName] = useState("");
  const [configFileName, setConfigFileName] = useState<string | null>(null);
  const [configContent, setConfigContent] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setWorkflowName("");
    setScriptContent(null);
    setScriptFileName("");
    setConfigFileName(null);
    setConfigContent(null);
    setIsUploading(false);
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
    setWorkflowName((current) => {
      if (current) {
        return current;
      }
      const frontmatter = parseWorkflowFrontmatter(selection.scriptContent);
      return frontmatter.name ?? selection.scriptName.replace(/\.py$/i, "");
    });
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

  const handleUpload = useCallback(async () => {
    if (!scriptContent || !workflowName.trim()) {
      return;
    }
    setIsUploading(true);
    setError(null);
    try {
      const stored = await uploadWorkflowFromFiles(
        workflowName.trim(),
        scriptContent,
        configContent,
      );
      toast({
        title: "Workflow uploaded",
        description: `"${stored.name}" has been added to your workspace.`,
      });
      handleOpenChange(false);
      navigate(
        getWorkspaceWorkflowPath(
          getSelectedWorkspaceSlug(),
          getWorkflowRouteRef(stored),
        ),
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Upload failed. Please try again.";
      setError(message);
      toast({
        title: "Upload failed",
        description: `${message} Fix the error and upload again.`,
        variant: "destructive",
      });
      if (err instanceof WorkflowUploadFailedError) {
        handleOpenChange(false);
        navigate(
          getWorkspaceWorkflowPath(
            getSelectedWorkspaceSlug(),
            getWorkflowRouteRef(err.workflow),
          ),
        );
      }
    } finally {
      setIsUploading(false);
    }
  }, [scriptContent, workflowName, configContent, handleOpenChange, navigate]);

  const canUpload = Boolean(scriptContent) && workflowName.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Upload Workflow</DialogTitle>
          <DialogDescription>
            Select a folder containing a Python workflow script and an optional
            JSON config to create a new workflow.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="upload-folder">
              Workflow Folder <span className="text-destructive">*</span>
            </Label>
            <WorkflowFolderPicker
              idPrefix="upload"
              scriptName={scriptFileName}
              configName={configFileName}
              onSelect={handleSelect}
              onError={handleSelectionError}
              disabled={isUploading}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="upload-name">Workflow Name</Label>
            <Input
              id="upload-name"
              value={workflowName}
              onChange={(e) => setWorkflowName(e.target.value)}
              placeholder="Enter workflow name"
              disabled={isUploading}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isUploading}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleUpload()}
            disabled={!canUpload || isUploading}
          >
            {isUploading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              <>
                <Upload className="mr-2 h-4 w-4" />
                Upload
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
