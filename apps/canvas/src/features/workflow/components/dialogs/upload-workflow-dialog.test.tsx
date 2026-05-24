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

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  uploadWorkflowFromFiles: uploadWorkflowFromFilesMock,
}));

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

describe("UploadWorkflowDialog", () => {
  beforeEach(() => {
    uploadWorkflowFromFilesMock.mockReset();
    navigateMock.mockReset();
    toastMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("uploads a script with config and navigates to the new workflow", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockResolvedValue({
      id: "uploaded-1",
      handle: "uploaded-handle",
      name: "my-workflow",
    });

    const onOpenChange = vi.fn();
    renderDialog({ onOpenChange });

    const scriptInput = screen.getByLabelText(/Workflow Script/i);
    await user.upload(scriptInput, pyFile("my-workflow.py", "print('hi')"));

    const configInput = screen.getByLabelText(/Config/i);
    await user.upload(
      configInput,
      jsonFile("config.json", JSON.stringify({ runtime: "python" })),
    );

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

  it("accepts python scripts with a generic mime type", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockResolvedValue({
      id: "uploaded-2",
      handle: "uploaded-handle-2",
      name: "generic-python",
    });

    renderDialog();

    const scriptInput = screen.getByLabelText(/Workflow Script/i);
    await user.upload(
      scriptInput,
      pyFile("generic-python.py", "print('hi')", "application/octet-stream"),
    );

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

  it("rejects oversized script files", async () => {
    const user = userEvent.setup();
    renderDialog();

    const scriptInput = screen.getByLabelText(/Workflow Script/i);
    await user.upload(
      scriptInput,
      pyFile("large.py", "x".repeat(1024 * 1024 + 1)),
    );

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

    const configInput = screen.getByLabelText(/Config/i);
    await user.upload(configInput, jsonFile("config.json", "{not-json"));

    await waitFor(() => {
      expect(
        screen.getByText(/config\.json is not valid JSON/i),
      ).toBeInTheDocument();
    });
  });

  it("disables upload until a script is selected", async () => {
    renderDialog();
    const uploadButton = screen.getByRole("button", { name: /^Upload$/ });
    expect(uploadButton).toBeDisabled();
  });

  it("shows error toast when upload fails", async () => {
    const user = userEvent.setup();
    uploadWorkflowFromFilesMock.mockRejectedValue(new Error("Boom"));
    renderDialog();

    const scriptInput = screen.getByLabelText(/Workflow Script/i);
    await user.upload(scriptInput, pyFile("flow.py", "print('hi')"));

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
