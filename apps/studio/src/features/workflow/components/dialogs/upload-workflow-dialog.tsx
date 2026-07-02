import { useCallback, useRef, useState } from "react";
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
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import { getWorkspaceWorkflowPath } from "@/lib/workspace-routing";

interface UploadWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const MAX_SCRIPT_UPLOAD_BYTES = 1024 * 1024;
const MAX_CONFIG_UPLOAD_BYTES = 256 * 1024;
const JSON_CONFIG_MIME_TYPES = new Set(["", "application/json", "text/json"]);

const formatUploadLimit = (bytes: number): string => {
  if (bytes >= 1024 * 1024) {
    return `${bytes / (1024 * 1024)} MB`;
  }
  return `${bytes / 1024} KB`;
};

const validateUploadFile = (
  file: File,
  options: {
    label: string;
    extension: string;
    maxBytes: number;
  },
): string | null => {
  if (!file.name.toLowerCase().endsWith(options.extension)) {
    return `${options.label} must use the ${options.extension} extension.`;
  }
  if (file.size > options.maxBytes) {
    return `${options.label} must be ${formatUploadLimit(options.maxBytes)} or smaller.`;
  }
  return null;
};

export function UploadWorkflowDialog({
  open,
  onOpenChange,
}: UploadWorkflowDialogProps) {
  const navigate = useNavigate();
  const scriptInputRef = useRef<HTMLInputElement>(null);
  const configInputRef = useRef<HTMLInputElement>(null);

  const [workflowName, setWorkflowName] = useState("");
  const [scriptContent, setScriptContent] = useState<string | null>(null);
  const [scriptFileName, setScriptFileName] = useState("");
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
    setConfigContent(null);
    setIsUploading(false);
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
      const validationError = validateUploadFile(file, {
        label: "Workflow script",
        extension: ".py",
        maxBytes: MAX_SCRIPT_UPLOAD_BYTES,
      });
      if (validationError) {
        setError(validationError);
        setScriptContent(null);
        setScriptFileName("");
        event.target.value = "";
        return;
      }
      setError(null);
      setScriptFileName(file.name);
      if (!workflowName) {
        const nameWithoutExt = file.name.replace(/\.py$/i, "");
        setWorkflowName(nameWithoutExt);
      }
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text === "string") {
          setScriptContent(text);
        }
      };
      reader.readAsText(file);
    },
    [workflowName],
  );

  const handleConfigFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }
      const validationError = validateUploadFile(file, {
        label: "Config file",
        extension: ".json",
        maxBytes: MAX_CONFIG_UPLOAD_BYTES,
        acceptedMimeTypes: JSON_CONFIG_MIME_TYPES,
      });
      if (validationError) {
        setError(validationError);
        setConfigContent(null);
        event.target.value = "";
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
            Upload a Python workflow script and an optional JSON config to
            create a new workflow.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="upload-script">
              Workflow Script <span className="text-destructive">*</span>
            </Label>
            <Input
              id="upload-script"
              ref={scriptInputRef}
              type="file"
              accept=".py"
              onChange={handleScriptFileChange}
              disabled={isUploading}
            />
            {scriptFileName && (
              <p className="text-xs text-muted-foreground">{scriptFileName}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="upload-config">Config (optional)</Label>
            <Input
              id="upload-config"
              ref={configInputRef}
              type="file"
              accept=".json"
              onChange={handleConfigFileChange}
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
