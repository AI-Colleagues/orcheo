// Exercise the backend's embedded email account form with the DOM Studio
// tests use. The mailbox view has no Studio screen of its own; it lives with
// the other MCP App view tests because Studio owns the JS test runner.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/email_account_form.html",
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
const setStatus = (text: string) => {
  element("status").textContent = text;
};

const account = {
  account_id: "acct-1",
  display_name: "Work",
  email_address: "me@example.com",
  labels: [],
  is_default: true,
  enabled: true,
  save_sent_copy: true,
};
const actions = ["save", "test", "delete"];
const picker = () => element("picker");
const select = () => element<HTMLSelectElement>("account-select");
const options = () => [...select().options].map((option) => option.text);
// Test and Remove only exist for a saved mailbox.
const managing = () =>
  ["test", "delete"].map((id) => !button(id).hidden && !button(id).disabled);

// The host only has to send the tool result while the tool runs, and `_meta`
// is what carries the token, so both can be missing on a restored view.
const open = (
  meta: object = { "orcheo/formToken": token },
  data: object = { accounts: [] },
) =>
  handlers["ui/notifications/tool-result"]({
    structuredContent: data,
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

describe("email account form MCP App", () => {
  it("keeps Save usable once the host delivers the private form token", () => {
    expect(status()).toContain("Waiting for the form data");

    open();

    expect(button("save").disabled).toBe(false);
    expect(status()).toBe("");
  });

  it("shows a first mailbox as the form itself, with no picker", () => {
    open();

    expect(picker().hidden).toBe(true);
    expect(button("save").textContent).toBe("Add mailbox");
    expect(managing()).toEqual([false, false]);
  });

  it("lists saved mailboxes with a way to add another", async () => {
    callTool.mockResolvedValueOnce({ structuredContent: { account } });
    open(undefined, { accounts: [account], account_id: account.account_id });

    await vi.waitFor(() =>
      expect(button("save").textContent).toBe("Save changes"),
    );
    expect(picker().hidden).toBe(false);
    expect(options()).toEqual([
      "Work · me@example.com · default",
      "Add another mailbox…",
    ]);
    expect(select().value).toBe(account.account_id);
    expect(element<HTMLInputElement>("email-address").value).toBe(
      account.email_address,
    );
    expect(managing()).toEqual([true, true]);

    select().value = "";
    select().dispatchEvent(new appWindow.Event("change"));

    await vi.waitFor(() =>
      expect(button("save").textContent).toBe("Add mailbox"),
    );
    expect(element<HTMLInputElement>("email-address").value).toBe("");
    expect(managing()).toEqual([false, false]);
    expect(callTool).toHaveBeenCalledTimes(1);
  });

  it("explains a view that never receives its tool result", () => {
    vi.advanceTimersByTime(8000);

    expect(status()).toContain("never received its data");
    expect(status()).toContain("open a new email account form");
    expect(element("status").className).toBe("error");
  });

  it("explains a tool result that arrives without the form token", () => {
    open({});

    expect(button("save").disabled).toBe(true);
    expect(status()).toContain("could not be verified");
    vi.advanceTimersByTime(8000);
    expect(status()).toContain("could not be verified");
  });

  it("refuses a Save pressed before any data arrived", () => {
    // Save is inert first, so no click reaches the handler at all.
    expect(button("save").disabled).toBe(true);
    button("save").click();
    expect(callTool).not.toHaveBeenCalled();
    expect(status()).toContain("Waiting for the form data");

    // Enabled by hand: the guard, not only the markup, must refuse.
    button("save").disabled = false;
    button("save").click();

    expect(callTool).not.toHaveBeenCalled();
    expect(status()).toContain("could not be verified");
  });

  it("explains a cancelled tool call and a failed handshake", async () => {
    handlers["ui/notifications/tool-cancelled"]({ reason: "user cancelled" });
    expect(status()).toContain("cancelled");

    frame.remove();
    mount(vi.fn().mockRejectedValue(new Error("no host")));
    await vi.waitFor(() =>
      expect(status()).toContain("could not reach the app host"),
    );
  });

  it("keeps every action inert until the data arrives", () => {
    for (const id of actions) expect(button(id).disabled).toBe(true);

    vi.advanceTimersByTime(8000);

    expect(status()).toContain("never received its data");
    for (const id of actions) expect(button(id).disabled).toBe(true);
  });

  it("offers no mailbox picker until the data arrives", () => {
    expect(picker().hidden).toBe(true);

    vi.advanceTimersByTime(8000);

    expect(picker().hidden).toBe(true);
    expect(status()).toContain("never received its data");
  });

  it("offers no mailbox picker without the form token", () => {
    open({}, { accounts: [account] });

    // Switching mailboxes needs the token, so there is nothing to offer.
    expect(picker().hidden).toBe(true);
    expect(status()).toContain("could not be verified");
  });

  it("explains Test and Remove pressed before any data arrived", () => {
    // Enabled by hand: the guards, not only the markup, must refuse.
    button("test").disabled = false;
    button("delete").disabled = false;

    button("test").click();
    expect(status()).toContain("could not be verified");

    setStatus("");
    button("delete").click();
    expect(status()).toContain("could not be verified");
    expect(callTool).not.toHaveBeenCalled();
  });

  it("leaves Remove inert once the last mailbox is gone", async () => {
    appWindow.confirm = () => true;
    callTool
      .mockResolvedValueOnce({ structuredContent: { account } })
      .mockResolvedValueOnce({ structuredContent: {} });
    open(undefined, {
      accounts: [account],
      account_id: account.account_id,
    });
    await vi.waitFor(() => expect(button("delete").disabled).toBe(false));

    button("delete").click();

    await vi.waitFor(() => expect(status()).toContain("removed"));
    expect(button("delete").disabled).toBe(true);
    expect(button("test").disabled).toBe(true);
    expect(managing()).toEqual([false, false]);
    expect(picker().hidden).toBe(true);
    expect(button("save").textContent).toBe("Add mailbox");
  });
});
