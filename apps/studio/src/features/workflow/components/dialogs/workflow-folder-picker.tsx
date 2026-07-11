import { useCallback, useEffect, useRef, useState } from "react";
import { FileJson, FileText, FolderUp, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  collectDroppedFolderFiles,
  collectFolderInputFiles,
  loadWorkflowFolderSelection,
  WorkflowFolderSelectionError,
  type WorkflowFolderSelection,
} from "@features/workflow/lib/workflow-folder-selection";

interface WorkflowFolderPickerProps {
  /** Unique prefix for the input id so labels stay associated per dialog. */
  idPrefix: string;
  disabled?: boolean;
  /** Name of the currently selected script, or "" when none is selected. */
  scriptName: string;
  /** Name of the currently selected config, or null when none is selected. */
  configName: string | null;
  onSelect: (selection: WorkflowFolderSelection) => void;
  onError: (message: string) => void;
}

const READ_FOLDER_FALLBACK_ERROR =
  "Could not read the selected folder. Please try again.";

export function WorkflowFolderPicker({
  idPrefix,
  disabled = false,
  scriptName,
  configName,
  onSelect,
  onError,
}: WorkflowFolderPickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const inputId = `${idPrefix}-folder`;

  // `webkitdirectory` is not part of the standard input typings, so set it
  // imperatively to opt the file picker into folder selection.
  useEffect(() => {
    const input = inputRef.current;
    if (input) {
      input.setAttribute("webkitdirectory", "");
      input.setAttribute("directory", "");
    }
  }, []);

  const processFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }
      setIsProcessing(true);
      try {
        const selection = await loadWorkflowFolderSelection(files);
        onSelect(selection);
      } catch (err) {
        const message =
          err instanceof WorkflowFolderSelectionError
            ? err.message
            : READ_FOLDER_FALLBACK_ERROR;
        onError(message);
      } finally {
        setIsProcessing(false);
      }
    },
    [onSelect, onError],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = collectFolderInputFiles(event.target.files);
      // Allow re-selecting the same folder later.
      event.target.value = "";
      void processFiles(files);
    },
    [processFiles],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLLabelElement>) => {
      event.preventDefault();
      setIsDragging(false);
      if (disabled) {
        return;
      }
      void (async () => {
        try {
          const files = await collectDroppedFolderFiles(event.dataTransfer);
          await processFiles(files);
        } catch {
          onError(READ_FOLDER_FALLBACK_ERROR);
        }
      })();
    },
    [disabled, processFiles, onError],
  );

  const handleDragOver = useCallback(
    (event: React.DragEvent<HTMLLabelElement>) => {
      event.preventDefault();
      if (!disabled) {
        setIsDragging(true);
      }
    },
    [disabled],
  );

  const handleDragLeave = useCallback(
    (event: React.DragEvent<HTMLLabelElement>) => {
      // Ignore drag-leave events bubbling up from child elements.
      if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
        return;
      }
      setIsDragging(false);
    },
    [],
  );

  const hasSelection = scriptName.length > 0;

  return (
    <div className="space-y-1.5">
      <label
        htmlFor={inputId}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        className={cn(
          "flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-input bg-transparent px-4 py-6 text-center transition-colors",
          "hover:border-ring focus-within:outline-none focus-within:ring-1 focus-within:ring-ring",
          disabled && "pointer-events-none opacity-50",
          isDragging && "border-ring bg-accent",
        )}
      >
        {isProcessing ? (
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        ) : (
          <FolderUp className="h-6 w-6 text-muted-foreground" />
        )}
        <span className="text-sm font-medium">
          {isDragging ? "Drop folder to load" : "Drag a folder here or browse"}
        </span>
        <span className="text-xs text-muted-foreground">
          The folder should contain one <code>.py</code> script and an optional{" "}
          <code>.json</code> config.
        </span>
      </label>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        multiple
        className="sr-only"
        onChange={handleInputChange}
        disabled={disabled}
      />
      {hasSelection && (
        <div className="space-y-1 rounded-md bg-muted/50 px-3 py-2 text-xs">
          <p className="flex items-center gap-1.5 text-foreground">
            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{scriptName}</span>
          </p>
          <p className="flex items-center gap-1.5 text-muted-foreground">
            <FileJson className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{configName ?? "No config file"}</span>
          </p>
        </div>
      )}
    </div>
  );
}
