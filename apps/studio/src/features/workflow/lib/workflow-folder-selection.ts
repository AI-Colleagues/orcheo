/**
 * Shared logic for selecting a workflow from a folder.
 *
 * A workflow folder is expected to contain exactly one Python (`.py`) script
 * and at most one JSON (`.json`) config file at its top level. Multiple files
 * of either kind are rejected as ambiguous so callers can surface an
 * easy-to-understand error. The helpers here work for both folder `<input>`
 * elements (`webkitdirectory`) and folder drag-and-drop.
 */

export const MAX_SCRIPT_UPLOAD_BYTES = 1024 * 1024;
export const MAX_CONFIG_UPLOAD_BYTES = 256 * 1024;

/** Error with a user-facing message describing why a folder was rejected. */
export class WorkflowFolderSelectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowFolderSelectionError";
  }
}

export interface WorkflowFolderSelection {
  scriptName: string;
  scriptContent: string;
  configName: string | null;
  configContent: Record<string, unknown> | null;
}

const formatUploadLimit = (bytes: number): string => {
  if (bytes >= 1024 * 1024) {
    return `${bytes / (1024 * 1024)} MB`;
  }
  return `${bytes / 1024} KB`;
};

const hasExtension = (name: string, extension: string): boolean =>
  name.toLowerCase().endsWith(extension);

const formatNames = (files: File[]): string =>
  files.map((file) => file.name).join(", ");

/**
 * Split a list of files into the single script and optional config.
 * Throws {@link WorkflowFolderSelectionError} when the folder is ambiguous or
 * is missing a script.
 */
export const classifyWorkflowFolderFiles = (
  files: File[],
): { script: File; config: File | null } => {
  const scripts = files.filter((file) => hasExtension(file.name, ".py"));
  const configs = files.filter((file) => hasExtension(file.name, ".json"));

  if (scripts.length === 0) {
    throw new WorkflowFolderSelectionError(
      "No Python (.py) workflow script was found in the folder. Add exactly one .py file and try again.",
    );
  }
  if (scripts.length > 1) {
    throw new WorkflowFolderSelectionError(
      `Found ${scripts.length} Python (.py) files (${formatNames(scripts)}). ` +
        "Keep exactly one .py file in the folder so it's clear which script to use.",
    );
  }
  if (configs.length > 1) {
    throw new WorkflowFolderSelectionError(
      `Found ${configs.length} JSON (.json) config files (${formatNames(configs)}). ` +
        "Keep at most one .json file in the folder so it's clear which config to use.",
    );
  }

  return { script: scripts[0], config: configs[0] ?? null };
};

const isTopLevelFile = (file: File): boolean => {
  const relativePath =
    (file as File & { webkitRelativePath?: string }).webkitRelativePath ?? "";
  // "" (no path info), "folder/file.py" → top level; "folder/sub/file.py" → nested.
  return relativePath.split("/").length <= 2;
};

/**
 * Collect the top-level files from a folder `<input webkitdirectory>` FileList,
 * ignoring anything inside nested subdirectories.
 */
export const collectFolderInputFiles = (fileList: FileList | null): File[] => {
  if (!fileList) {
    return [];
  }
  return Array.from(fileList).filter(isTopLevelFile);
};

interface FileSystemEntryLike {
  isFile: boolean;
  isDirectory: boolean;
  file?: (
    onSuccess: (file: File) => void,
    onError?: (err: unknown) => void,
  ) => void;
  createReader?: () => {
    readEntries: (
      onSuccess: (entries: FileSystemEntryLike[]) => void,
      onError?: (err: unknown) => void,
    ) => void;
  };
}

const entryToFile = (entry: FileSystemEntryLike): Promise<File> =>
  new Promise((resolve, reject) => {
    if (!entry.file) {
      reject(new Error("Unable to read dropped file."));
      return;
    }
    entry.file(resolve, reject);
  });

const readDirectoryEntries = (
  directory: FileSystemEntryLike,
): Promise<FileSystemEntryLike[]> =>
  new Promise((resolve, reject) => {
    const reader = directory.createReader?.();
    if (!reader) {
      resolve([]);
      return;
    }
    const entries: FileSystemEntryLike[] = [];
    const readBatch = () => {
      reader.readEntries((batch) => {
        // readEntries returns results in batches; an empty batch marks the end.
        if (batch.length === 0) {
          resolve(entries);
          return;
        }
        entries.push(...batch);
        readBatch();
      }, reject);
    };
    readBatch();
  });

/**
 * Collect the top-level files from a folder drag-and-drop event. Supports
 * dropping a folder (its immediate children are used) or dropping the loose
 * files directly. Falls back to `dataTransfer.files` when the entry API is
 * unavailable.
 */
export const collectDroppedFolderFiles = async (
  dataTransfer: DataTransfer,
): Promise<File[]> => {
  const items = dataTransfer.items ? Array.from(dataTransfer.items) : [];
  const entries: FileSystemEntryLike[] = [];
  for (const item of items) {
    const entry =
      typeof item.webkitGetAsEntry === "function"
        ? item.webkitGetAsEntry()
        : null;
    if (entry) {
      entries.push(entry as unknown as FileSystemEntryLike);
    }
  }

  if (entries.length === 0) {
    return Array.from(dataTransfer.files ?? []);
  }

  const files: File[] = [];
  for (const entry of entries) {
    if (entry.isFile) {
      files.push(await entryToFile(entry));
    } else if (entry.isDirectory) {
      const children = await readDirectoryEntries(entry);
      for (const child of children) {
        if (child.isFile) {
          files.push(await entryToFile(child));
        }
      }
    }
  }
  return files;
};

const readFileText = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") {
        resolve(result);
      } else {
        reject(new Error(`Unable to read ${file.name}.`));
      }
    };
    reader.onerror = () =>
      reject(reader.error ?? new Error(`Unable to read ${file.name}.`));
    reader.readAsText(file);
  });

/**
 * Classify, validate, and read the workflow script and optional config from a
 * folder's files. Throws {@link WorkflowFolderSelectionError} with a
 * user-facing message on any problem.
 */
export const loadWorkflowFolderSelection = async (
  files: File[],
): Promise<WorkflowFolderSelection> => {
  const { script, config } = classifyWorkflowFolderFiles(files);

  if (script.size > MAX_SCRIPT_UPLOAD_BYTES) {
    throw new WorkflowFolderSelectionError(
      `Workflow script must be ${formatUploadLimit(MAX_SCRIPT_UPLOAD_BYTES)} or smaller.`,
    );
  }
  const scriptContent = await readFileText(script);

  let configName: string | null = null;
  let configContent: Record<string, unknown> | null = null;
  if (config) {
    if (config.size > MAX_CONFIG_UPLOAD_BYTES) {
      throw new WorkflowFolderSelectionError(
        `Config file must be ${formatUploadLimit(MAX_CONFIG_UPLOAD_BYTES)} or smaller.`,
      );
    }
    const configText = await readFileText(config);
    try {
      configContent = JSON.parse(configText) as Record<string, unknown>;
    } catch {
      throw new WorkflowFolderSelectionError(
        `${config.name} is not valid JSON.`,
      );
    }
    configName = config.name;
  }

  return {
    scriptName: script.name,
    scriptContent,
    configName,
    configContent,
  };
};
