// Exercise the backend's embedded credential form with the DOM Studio tests use.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/credential_form.html",
  ),
  "utf8",
);
const token = "private-form-token";
const secret = "sk-live-must-never-reach-the-model";
const callTool = vi.fn();
const tellModel = vi.fn();
let handlers: Record<string, (params: object) => void>;
let frame: HTMLIFrameElement;
let appWindow: Window;

const element = <T extends HTMLElement = HTMLElement>(id: string): T =>
  appWindow.document.getElementById(id) as T;
const input = (id: string) => element<HTMLInputElement>(id);
const button = (id: string) => element<HTMLButtonElement>(id);
const status = () => element("status").textContent ?? "";

// Deliver the tool result the host is only obliged to send while the tool
// runs; `meta` is dropped by hosts that restore a view without it.
const open = (
  data: object = {},
  meta: object = { "orcheo/formToken": token },
) =>
  handlers["ui/notifications/tool-result"]({
    structuredContent: { name: "openai_key", provider: "openai", ...data },
    _meta: meta,
  });

const mount = (connect = vi.fn().mockResolvedValue({})) => {
  handlers = {};
  frame = document.createElement("iframe");
  document.body.append(frame);
  appWindow = frame.contentWindow!;
  const parsed = new DOMParser().parseFromString(html, "text/html");
  appWindow.document.body.innerHTML = parsed.body.innerHTML;
  const bridge = {
    callTool,
    tellModel,
    connect,
    on: (name: string, handler: (params: object) => void) => {
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
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  tellModel.mockResolvedValue(undefined);
  callTool.mockResolvedValue({
    structuredContent: { id: "cred-1", name: "openai_key", provider: "openai" },
  });
  mount();
});

afterEach(() => {
  frame.remove();
  vi.useRealTimers();
});

describe("credential form MCP App", () => {
  it("enables Save once the host delivers the private form token", async () => {
    expect(button("save").disabled).toBe(true);
    expect(status()).toContain("Waiting for the form data");

    open();

    expect(button("save").disabled).toBe(false);
    expect(status()).toBe("");
    expect(input("name").value).toBe("openai_key");
    input("secret").value = secret;
    button("save").click();
    await vi.waitFor(() => expect(tellModel).toHaveBeenCalledTimes(1));
    expect(callTool).toHaveBeenCalledExactlyOnceWith("save_credential", {
      form_token: token,
      name: "openai_key",
      provider: "openai",
      access: "shared",
      secret,
    });
    expect(JSON.stringify(tellModel.mock.calls)).not.toContain(secret);
    expect(JSON.stringify(tellModel.mock.calls)).not.toContain(token);
  });

  it("explains a view that never receives its tool result", () => {
    vi.advanceTimersByTime(8000);

    expect(status()).toContain("never received its data");
    expect(status()).toContain("open a new credential form");
    expect(element("status").className).toBe("error");
    expect(button("save").disabled).toBe(true);
  });

  it("explains a tool result that arrives without the form token", () => {
    open({}, {});

    expect(button("save").disabled).toBe(true);
    expect(status()).toContain("could not be verified");
    // The data arrived, so the stalled-view message must stay away.
    vi.advanceTimersByTime(8000);
    expect(status()).toContain("could not be verified");
    button("save").click();
    expect(callTool).not.toHaveBeenCalled();
  });

  it("explains a cancelled tool call", () => {
    handlers["ui/notifications/tool-cancelled"]({ reason: "user cancelled" });

    expect(status()).toContain("cancelled");
    expect(status()).toContain("open a new credential form");
    expect(button("save").disabled).toBe(true);
    vi.advanceTimersByTime(8000);
    expect(status()).toContain("cancelled");
  });

  it("explains a host that never completes the handshake", async () => {
    frame.remove();
    mount(vi.fn().mockRejectedValue(new Error("no host")));

    await vi.waitFor(() =>
      expect(status()).toContain("could not reach the app host"),
    );
    expect(button("save").disabled).toBe(true);
  });

  it("keeps a delivered tool result after a late handshake failure", async () => {
    frame.remove();
    let reject: (error: Error) => void = () => undefined;
    mount(vi.fn().mockReturnValue(new Promise((_, no) => (reject = no))));
    open();
    reject(new Error("too late"));

    await vi.waitFor(() => expect(button("save").disabled).toBe(false));
    expect(status()).toBe("");
  });
});
