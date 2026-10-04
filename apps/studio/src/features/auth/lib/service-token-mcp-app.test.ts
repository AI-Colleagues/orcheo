// Exercise the backend's embedded App HTML with the same DOM used by Studio tests.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/service_token_form.html",
  ),
  "utf8",
);
const secret = "test-secret-only-for-the-private-view";
const created = {
  content: [{ type: "text", text: "Service token created." }],
  structuredContent: { identifier: "token-123", name: "Automation" },
  _meta: { "orcheo/serviceTokenSecret": secret },
};
const callTool = vi.fn();
const tellModel = vi.fn();
const clipboard = vi.fn();
let handlers: Record<string, (result: object) => void>;
let frame: HTMLIFrameElement;
let appWindow: Window;

const element = <T extends HTMLElement = HTMLElement>(id: string): T =>
  appWindow.document.getElementById(id) as T;
const input = (id: string) => element<HTMLInputElement>(id);
const button = (id: string) => element<HTMLButtonElement>(id);
const open = (data: object = {}) =>
  handlers["ui/notifications/tool-result"]({
    structuredContent: {
      action: "create",
      workspace: "team",
      name: "Automation",
      scopes: ["workflows:read"],
      available_scopes: ["workflows:read", "admin:tokens:write"],
      ...data,
    },
    _meta: { "orcheo/formToken": "private-form-token" },
  });

beforeEach(() => {
  vi.clearAllMocks();
  handlers = {};
  tellModel.mockResolvedValue(undefined);
  clipboard.mockResolvedValue(undefined);
  callTool.mockResolvedValue(created);
  frame = document.createElement("iframe");
  document.body.append(frame);
  appWindow = frame.contentWindow!;
  const parsed = new DOMParser().parseFromString(html, "text/html");
  appWindow.document.body.innerHTML = parsed.body.innerHTML;
  Object.defineProperty(appWindow.navigator, "clipboard", {
    value: { writeText: clipboard },
  });
  const bridge = {
    callTool,
    tellModel,
    connect: vi.fn(),
    on: (name: string, handler: (result: object) => void) => {
      handlers[name] = handler;
    },
  };
  // Execute the view's actual script against an isolated iframe, with only
  // the host bridge mocked. Inserting HTML alone does not execute scripts.
  const script = parsed.querySelectorAll("script")[1].textContent!;
  new Function("orcheoBridge", "window", "document", "navigator", script)(
    bridge,
    appWindow,
    appWindow.document,
    appWindow.navigator,
  );
});

afterEach(() => frame.remove());

describe("service token MCP App", () => {
  it("creates through the private tool and never sends a secret to model context", async () => {
    expect(button("submit").disabled).toBe(true);
    open({ expires_in_seconds: 3600 });
    button("submit").click();
    await vi.waitFor(() => expect(input("secret").value).toBe(secret));
    expect(callTool).toHaveBeenCalledExactlyOnceWith("create_service_token", {
      form_token: "private-form-token",
      name: "Automation",
      scopes: ["workflows:read"],
      expires_in_seconds: 3600,
    });
    expect(input("secret").type).toBe("password");
    expect(button("submit").disabled).toBe(true);
    button("submit").click();
    expect(callTool).toHaveBeenCalledTimes(1);
    expect(tellModel).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(tellModel.mock.calls)).not.toContain(secret);
    expect(JSON.stringify(tellModel.mock.calls)).not.toContain("private-form-token");
    expect(appWindow.localStorage.length).toBe(0);
    expect(appWindow.sessionStorage.length).toBe(0);
  });

  it("reveals, copies, and clears only inside the view", async () => {
    open();
    button("submit").click();
    await vi.waitFor(() => expect(input("secret").value).toBe(secret));
    button("reveal").click();
    expect(input("secret").type).toBe("text");
    button("copy").click();
    await vi.waitFor(() => expect(clipboard).toHaveBeenCalledWith(secret));
    button("clear").click();
    expect(input("secret").value).toBe("");
    expect(element("secret-panel").hidden).toBe(true);
    expect(JSON.stringify(tellModel.mock.calls)).not.toContain(secret);
  });

  it("revokes only the form's bound token and requires a reason", async () => {
    callTool.mockResolvedValue({ structuredContent: { revoked: true } });
    open({ action: "revoke", token: { name: "<img src=x>", identifier: "id-1" } });
    expect(element("token-info").textContent).toContain("<img src=x>");
    expect(element("token-info").querySelector("img")).toBeNull();
    button("submit").click();
    expect(callTool).not.toHaveBeenCalled();
    input("reason").value = "Replaced";
    button("submit").click();
    await vi.waitFor(() => expect(tellModel).toHaveBeenCalledTimes(1));
    expect(callTool).toHaveBeenCalledExactlyOnceWith("revoke_service_token", {
      form_token: "private-form-token",
      reason: "Replaced",
    });
    expect(element("secret-panel").hidden).toBe(true);
    expect(button("submit").disabled).toBe(true);
  });

  it("validates expiration and allows correcting a failed submission", async () => {
    open();
    input("expiry").value = "2";
    button("submit").click();
    expect(callTool).not.toHaveBeenCalled();
    expect(element("status").textContent).toContain("60 whole seconds");
    input("expiry").value = "";
    callTool.mockRejectedValueOnce(new Error("Permission denied"));
    button("submit").click();
    await vi.waitFor(() => expect(button("submit").disabled).toBe(false));
    expect(element("status").textContent).toBe("Permission denied");
    expect(tellModel).not.toHaveBeenCalled();
    button("submit").click();
    await vi.waitFor(() => expect(input("secret").value).toBe(secret));
  });

  it("never falls back to model-visible content when private metadata is missing", async () => {
    callTool.mockResolvedValue({
      content: [{ text: secret }],
      structuredContent: { secret },
    });
    open();
    button("submit").click();
    await vi.waitFor(() =>
      expect(element("status").textContent).toContain(
        "did not deliver the private secret",
      ),
    );
    expect(input("secret").value).toBe("");
    expect(button("submit").disabled).toBe(true);
    expect(tellModel).not.toHaveBeenCalled();
  });

  it.each(["teardown", "pagehide"])("discards a pending secret response on %s", async (event) => {
    let finish!: (result: typeof created) => void;
    callTool.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    open();
    button("submit").click();
    if (event === "teardown") handlers["ui/resource-teardown"]({});
    else appWindow.dispatchEvent(new Event("pagehide"));
    finish(created);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(input("secret").value).toBe("");
    expect(element("secret-panel").hidden).toBe(true);
    expect(tellModel).not.toHaveBeenCalled();
  });

  it("clears displayed secrets on pagehide", async () => {
    open();
    button("submit").click();
    await vi.waitFor(() => expect(input("secret").value).toBe(secret));
    appWindow.dispatchEvent(new Event("pagehide"));
    expect(input("secret").value).toBe("");
    expect(element("secret-panel").hidden).toBe(true);
  });

  it("stops when the host omits the private form capability", () => {
    handlers["ui/notifications/tool-result"]({ structuredContent: { action: "create" } });
    expect(button("submit").disabled).toBe(true);
    expect(element("status").textContent).toContain("did not deliver the private form");
    expect(callTool).not.toHaveBeenCalled();
  });
});
