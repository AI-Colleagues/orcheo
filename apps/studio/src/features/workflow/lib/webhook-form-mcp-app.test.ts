// Exercise the backend's embedded webhook form with the DOM Studio tests use.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/webhook_form.html",
  ),
  "utf8",
);
const token = "private-form-token";
const callTool = vi.fn();
const tellModel = vi.fn();
let handlers: Record<string, (params: object) => void>;
let frame: HTMLIFrameElement;
let appWindow: Window;

const element = <T extends HTMLElement = HTMLElement>(id: string): T =>
  appWindow.document.getElementById(id) as T;
const button = (id: string) => element<HTMLButtonElement>(id);
const status = () => element("status").textContent ?? "";

// The host only has to send the tool result while the tool runs, and `_meta`
// is what carries the token, so both can be missing on a restored view.
const open = (meta: object = { "orcheo/formToken": token }) =>
  handlers["ui/notifications/tool-result"]({
    structuredContent: { config: { secret_header: "x-hook" } },
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
  callTool.mockResolvedValue({ structuredContent: {} });
  mount();
});

afterEach(() => {
  frame.remove();
  vi.useRealTimers();
});

describe("webhook form MCP App", () => {
  it("enables Save once the host delivers the private form token", async () => {
    expect(button("save").disabled).toBe(true);
    expect(status()).toContain("Waiting for the form data");

    open();

    expect(button("save").disabled).toBe(false);
    expect(status()).toBe("");
    button("save").click();
    await vi.waitFor(() => expect(tellModel).toHaveBeenCalledTimes(1));
    expect(callTool).toHaveBeenCalledExactlyOnceWith("save_webhook_config", {
      form_token: token,
      changes: {},
    });
  });

  it("explains a view that never receives its tool result", () => {
    vi.advanceTimersByTime(8000);

    expect(status()).toContain("never received its data");
    expect(status()).toContain("open a new webhook form");
    expect(element("status").className).toBe("error");
    expect(button("save").disabled).toBe(true);
  });

  it("explains a tool result that arrives without the form token", () => {
    open({});

    expect(button("save").disabled).toBe(true);
    expect(status()).toContain("could not be verified");
    vi.advanceTimersByTime(8000);
    expect(status()).toContain("could not be verified");
    button("save").click();
    expect(callTool).not.toHaveBeenCalled();
  });

  it("explains a cancelled tool call and a failed handshake", async () => {
    handlers["ui/notifications/tool-cancelled"]({ reason: "user cancelled" });
    expect(status()).toContain("cancelled");
    expect(button("save").disabled).toBe(true);

    frame.remove();
    mount(vi.fn().mockRejectedValue(new Error("no host")));
    await vi.waitFor(() =>
      expect(status()).toContain("could not reach the app host"),
    );
    expect(button("save").disabled).toBe(true);
  });
});
