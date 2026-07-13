import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkflowFolderPicker } from "./workflow-folder-picker";

const file = (name: string, contents = "", type = "text/plain") =>
  new File([contents], name, { type });

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

const dropData = (entries: FakeEntry[]) => ({
  items: entries.map((entry) => ({ webkitGetAsEntry: () => entry })),
  files: [],
});

const renderPicker = (
  overrides?: Partial<React.ComponentProps<typeof WorkflowFolderPicker>>,
) => {
  const onSelect = vi.fn();
  const onError = vi.fn();
  render(
    <WorkflowFolderPicker
      idPrefix="test"
      scriptName=""
      configName={null}
      onSelect={onSelect}
      onError={onError}
      {...overrides}
    />,
  );
  return { onSelect, onError };
};

const dropzone = () =>
  screen.getByText(/Drag a folder here/i).closest("label")!;

describe("WorkflowFolderPicker drag-and-drop", () => {
  afterEach(() => {
    cleanup();
  });

  it("loads a dropped folder and reports the selection", async () => {
    const { onSelect, onError } = renderPicker();

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([
        directoryEntry([
          fileEntry(file("flow.py", "print('hi')")),
          fileEntry(file("config.json", JSON.stringify({ a: 1 }))),
        ]),
      ]),
    });

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith({
        scriptName: "flow.py",
        scriptContent: "print('hi')",
        configName: "config.json",
        configContent: { a: 1 },
      });
    });
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports an ambiguity error when a dropped folder has two scripts", async () => {
    const { onSelect, onError } = renderPicker();

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([
        directoryEntry([
          fileEntry(file("a.py", "print('a')")),
          fileEntry(file("b.py", "print('b')")),
        ]),
      ]),
    });

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        expect.stringMatching(/Found 2 Python \(\.py\) files/i),
      );
    });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("reports an error when a dropped folder has no usable files", async () => {
    const { onSelect, onError } = renderPicker();

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([
        directoryEntry([directoryEntry([fileEntry(file("nested.py"))])]),
      ]),
    });

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        expect.stringMatching(/No files were found in the selected folder/i),
      );
    });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("clears a stale selection when a subsequent drop yields no files", async () => {
    const { onSelect, onError } = renderPicker();

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([
        directoryEntry([fileEntry(file("flow.py", "print('hi')"))]),
      ]),
    });
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([
        directoryEntry([directoryEntry([fileEntry(file("nested.py"))])]),
      ]),
    });

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("ignores drops while disabled", async () => {
    const { onSelect, onError } = renderPicker({ disabled: true });

    fireEvent.drop(dropzone(), {
      dataTransfer: dropData([fileEntry(file("flow.py", "print('hi')"))]),
    });

    // Give any pending microtasks a chance to run before asserting no-op.
    await Promise.resolve();
    expect(onSelect).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });
});
