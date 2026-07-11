import { describe, expect, it } from "vitest";

import {
  classifyWorkflowFolderFiles,
  collectDroppedFolderFiles,
  collectFolderInputFiles,
  loadWorkflowFolderSelection,
  WorkflowFolderSelectionError,
} from "./workflow-folder-selection";

const file = (name: string, contents = "", type = "text/plain") =>
  new File([contents], name, { type });

const withPath = (target: File, relativePath: string): File => {
  Object.defineProperty(target, "webkitRelativePath", {
    value: relativePath,
    configurable: true,
  });
  return target;
};

const asFileList = (files: File[]): FileList => {
  const list: Record<number, File> & {
    length: number;
    item: (index: number) => File | null;
    [Symbol.iterator]: () => IterableIterator<File>;
  } = {
    length: files.length,
    item: (index: number) => files[index] ?? null,
    [Symbol.iterator]: function* () {
      yield* files;
    },
  };
  files.forEach((entry, index) => {
    list[index] = entry;
  });
  return list as unknown as FileList;
};

interface FakeEntry {
  isFile: boolean;
  isDirectory: boolean;
  file?: (onSuccess: (file: File) => void) => void;
  createReader?: () => {
    readEntries: (onSuccess: (entries: FakeEntry[]) => void) => void;
  };
}

const fileEntry = (target: File): FakeEntry => ({
  isFile: true,
  isDirectory: false,
  file: (onSuccess) => onSuccess(target),
});

const directoryEntry = (children: FakeEntry[]): FakeEntry => ({
  isFile: false,
  isDirectory: true,
  createReader: () => {
    let emitted = false;
    return {
      readEntries: (onSuccess) => {
        if (emitted) {
          onSuccess([]);
          return;
        }
        emitted = true;
        onSuccess(children);
      },
    };
  },
});

const asDataTransfer = (
  entries: FakeEntry[],
  files: File[] = [],
): DataTransfer =>
  ({
    items: entries.map((entry) => ({ webkitGetAsEntry: () => entry })),
    files: asFileList(files),
  }) as unknown as DataTransfer;

describe("classifyWorkflowFolderFiles", () => {
  it("returns the single script and optional config", () => {
    const script = file("flow.py");
    const config = file("config.json");
    expect(classifyWorkflowFolderFiles([config, script])).toEqual({
      script,
      config,
    });
  });

  it("allows a script without a config", () => {
    const script = file("flow.py");
    expect(classifyWorkflowFolderFiles([script])).toEqual({
      script,
      config: null,
    });
  });

  it("throws when there is no python script", () => {
    expect(() => classifyWorkflowFolderFiles([file("config.json")])).toThrow(
      WorkflowFolderSelectionError,
    );
    expect(() => classifyWorkflowFolderFiles([file("config.json")])).toThrow(
      /No Python \(\.py\) workflow script/i,
    );
  });

  it("throws on multiple python scripts", () => {
    expect(() =>
      classifyWorkflowFolderFiles([file("a.py"), file("b.py")]),
    ).toThrow(/Found 2 Python \(\.py\) files \(a\.py, b\.py\)/i);
  });

  it("throws on multiple json configs", () => {
    expect(() =>
      classifyWorkflowFolderFiles([
        file("flow.py"),
        file("a.json"),
        file("b.json"),
      ]),
    ).toThrow(/Found 2 JSON \(\.json\) config files/i);
  });
});

describe("collectFolderInputFiles", () => {
  it("returns [] for a null list", () => {
    expect(collectFolderInputFiles(null)).toEqual([]);
  });

  it("keeps only top-level files, ignoring nested directories", () => {
    const topScript = withPath(file("flow.py"), "workflow/flow.py");
    const topConfig = withPath(file("config.json"), "workflow/config.json");
    const nested = withPath(
      file("cached.py"),
      "workflow/__pycache__/cached.py",
    );

    const result = collectFolderInputFiles(
      asFileList([topScript, topConfig, nested]),
    );

    expect(result).toEqual([topScript, topConfig]);
  });

  it("keeps files that have no relative path info", () => {
    const loose = file("flow.py");
    expect(collectFolderInputFiles(asFileList([loose]))).toEqual([loose]);
  });
});

describe("collectDroppedFolderFiles", () => {
  it("reads only top-level files from a dropped folder", async () => {
    const script = file("flow.py");
    const config = file("config.json");
    const dropped = asDataTransfer([
      directoryEntry([
        fileEntry(script),
        fileEntry(config),
        directoryEntry([fileEntry(file("nested.py"))]),
      ]),
    ]);

    const result = await collectDroppedFolderFiles(dropped);

    expect(result).toEqual([script, config]);
  });

  it("supports dropping loose files directly", async () => {
    const script = file("flow.py");
    const config = file("config.json");
    const dropped = asDataTransfer([fileEntry(script), fileEntry(config)]);

    const result = await collectDroppedFolderFiles(dropped);

    expect(result).toEqual([script, config]);
  });

  it("falls back to dataTransfer.files when the entry API is unavailable", async () => {
    const script = file("flow.py");
    const dropped = asDataTransfer([], [script]);

    const result = await collectDroppedFolderFiles(dropped);

    expect(result).toEqual([script]);
  });
});

describe("loadWorkflowFolderSelection", () => {
  it("reads the script and parses the config", async () => {
    const selection = await loadWorkflowFolderSelection([
      file("flow.py", "print('hi')"),
      file("config.json", JSON.stringify({ runtime: "python" })),
    ]);

    expect(selection).toEqual({
      scriptName: "flow.py",
      scriptContent: "print('hi')",
      configName: "config.json",
      configContent: { runtime: "python" },
    });
  });

  it("returns a null config when none is present", async () => {
    const selection = await loadWorkflowFolderSelection([
      file("flow.py", "print('hi')"),
    ]);

    expect(selection.configName).toBeNull();
    expect(selection.configContent).toBeNull();
  });

  it("throws on invalid config json", async () => {
    await expect(
      loadWorkflowFolderSelection([
        file("flow.py", "print('hi')"),
        file("config.json", "{not-json"),
      ]),
    ).rejects.toThrow(/config\.json is not valid JSON/i);
  });

  it("throws on an oversized script", async () => {
    await expect(
      loadWorkflowFolderSelection([
        file("flow.py", "x".repeat(1024 * 1024 + 1)),
      ]),
    ).rejects.toThrow(/Workflow script must be 1 MB or smaller/i);
  });
});
