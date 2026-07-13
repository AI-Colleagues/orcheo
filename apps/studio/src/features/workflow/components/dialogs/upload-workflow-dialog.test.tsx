import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadWorkflowDialog } from "./upload-workflow-dialog";

const { uploadWorkflowFromFilesMock, navigateMock, toastMock } = vi.hoisted(
  () => ({
    uploadWorkflowFromFilesMock: vi.fn(),
    navigateMock: vi.fn(),
    toastMock: vi.fn(),
  }),
);

vi.mock("@features/workflow/lib/workflow-storage", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("@features/workflow/lib/workflow-storage")
    >();
  return {
    ...actual,
    uploadWorkflowFromFiles: uploadWorkflowFromFilesMock,
  };
});

vi.mock("@features/workflow/lib/workflow-storage-helpers", () => ({
  getWorkflowRouteRef: (workflow: { id: string; handle?: string | null }) =>
    workflow.handle ?? workflow.id,
}));

vi.mock("@/lib/workspace-session", () => ({
  getSelectedWorkspaceSlug: () => "acme",
}));

vi.mock("@/hooks/use-toast", () => ({
  toast: toastMock,
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

const renderDialog = (overrides?: { onOpenChange?: (open: boolean) => void }) =>
  render(
    <MemoryRouter>
      <UploadWorkflowDialog
        open={true}
        onOpenChange={overrides?.onOpenChange ?? (() => {})}
      />
    </MemoryRouter>,
  );

const pyFile = (
  name: string,
  contents: string,
  type: string = "text/x-python",
) => new File([contents], name, { type });

const jsonFile = (name: string, contents: string) =>
  new File([contents], name, { type: "application/json" });

const getFolderInput = () =>
  screen.getByLabelText(/Workflow Folder/i) as HTMLInputElement;

describe("UploadWorkflowDialog", () => {
  beforeEach(() => {
    uploadWorkflowFromFilesMock.mockReset();
    navigateMock.mockReset();
    toastMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("uploads a folder with script and config and navigates to the new workflow", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockResolvedValue({
      id: "uploaded-1",
      handle: "uploaded-handle",
      name: "my-workflow",
    });

    const onOpenChange = vi.fn();
    renderDialog({ onOpenChange });

    await user.upload(getFolderInput(), [
      pyFile("my-workflow.py", "print('hi')"),
      jsonFile("config.json", JSON.stringify({ runtime: "python" })),
    ]);

    await waitFor(() => {
      expect(
        (screen.getByLabelText(/Workflow Name/i) as HTMLInputElement).value,
      ).toBe("my-workflow");
    });

    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    await waitFor(() => {
      expect(uploadWorkflowFromFilesMock).toHaveBeenCalledWith(
        "my-workflow",
        "print('hi')",
        { runtime: "python" },
      );
    });

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
    expect(navigateMock).toHaveBeenCalledWith("/acme/uploaded-handle");
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Workflow uploaded" }),
    );
  });

  it("uploads a folder that has only a script", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockResolvedValue({
      id: "uploaded-2",
      handle: "uploaded-handle-2",
      name: "generic-python",
    });

    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile("generic-python.py", "print('hi')", "application/octet-stream"),
    ]);

    await waitFor(() => {
      expect(
        (screen.getByLabelText(/Workflow Name/i) as HTMLInputElement).value,
      ).toBe("generic-python");
    });

    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    await waitFor(() => {
      expect(uploadWorkflowFromFilesMock).toHaveBeenCalledWith(
        "generic-python",
        "print('hi')",
        null,
      );
    });
  });

  it("defaults the workflow name from script frontmatter", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockResolvedValue({
      id: "uploaded-frontmatter",
      handle: "frontmatter-handle",
      name: "Frontmatter Name",
    });

    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile(
        "fallback.py",
        `# /// orcheo
# name = "Frontmatter Name"
# ///

print('hi')
`,
      ),
    ]);

    await waitFor(() => {
      expect(
        (screen.getByLabelText(/Workflow Name/i) as HTMLInputElement).value,
      ).toBe("Frontmatter Name");
    });

    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    await waitFor(() => {
      expect(uploadWorkflowFromFilesMock).toHaveBeenCalledWith(
        "Frontmatter Name",
        expect.stringContaining('name = "Frontmatter Name"'),
        null,
      );
    });
  });

  it("rejects a folder with multiple python scripts as ambiguous", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile("first.py", "print('a')"),
      pyFile("second.py", "print('b')"),
    ]);

    await waitFor(() => {
      expect(
        screen.getByText(/Found 2 Python \(\.py\) files/i),
      ).toBeInTheDocument();
    });
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Couldn't use that folder",
        variant: "destructive",
      }),
    );
    expect(screen.getByRole("button", { name: /^Upload$/ })).toBeDisabled();
  });

  it("rejects a folder with multiple json configs as ambiguous", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile("flow.py", "print('a')"),
      jsonFile("a.json", "{}"),
      jsonFile("b.json", "{}"),
    ]);

    await waitFor(() => {
      expect(
        screen.getByText(/Found 2 JSON \(\.json\) config files/i),
      ).toBeInTheDocument();
    });
  });

  it("rejects a folder without a python script", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.upload(getFolderInput(), [jsonFile("config.json", "{}")]);

    await waitFor(() => {
      expect(
        screen.getByText(/No Python \(\.py\) workflow script was found/i),
      ).toBeInTheDocument();
    });
  });

  it("rejects oversized script files", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile("large.py", "x".repeat(1024 * 1024 + 1)),
    ]);

    await waitFor(() => {
      expect(
        screen.getByText(/Workflow script must be 1 MB or smaller/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /^Upload$/ })).toBeDisabled();
  });

  it("rejects invalid json config", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.upload(getFolderInput(), [
      pyFile("flow.py", "print('hi')"),
      jsonFile("config.json", "{not-json"),
    ]);

    await waitFor(() => {
      expect(
        screen.getByText(/config\.json is not valid JSON/i),
      ).toBeInTheDocument();
    });
  });

  it("disables upload until a folder is selected", async () => {
    renderDialog();
    const uploadButton = screen.getByRole("button", { name: /^Upload$/ });
    expect(uploadButton).toBeDisabled();
  });

  it("shows error toast when upload fails", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockRejectedValue(new Error("Boom"));
    renderDialog();

    await user.upload(getFolderInput(), [pyFile("flow.py", "print('hi')")]);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Upload$/ })).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Upload failed",
          variant: "destructive",
        }),
      );
    });
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
