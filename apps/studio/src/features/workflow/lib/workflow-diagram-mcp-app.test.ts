// Exercise the backend's embedded workflow diagram with the DOM Studio tests
// use. Only the host bridge and the Mermaid CDN import are replaced.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/workflow_diagram.html",
  ),
  "utf8",
);
const graph = "graph TD; start --> finish";
const mermaid = {
  initialize: vi.fn(),
  render: vi.fn(),
};
let handlers: Record<string, (params: object) => void>;
let connect: ReturnType<typeof vi.fn>;
let loadModule: ReturnType<typeof vi.fn>;
let frame: HTMLIFrameElement;
let appWindow: Window;

const element = (id: string) => appWindow.document.getElementById(id)!;
const status = () => element("status").textContent ?? "";
const show = () =>
  handlers["ui/notifications/tool-result"]({
    structuredContent: { name: "Billing", version: 3, mermaid: graph },
  });

const mount = () => {
  handlers = {};
  frame = document.createElement("iframe");
  document.body.append(frame);
  appWindow = frame.contentWindow!;
  const parsed = new DOMParser().parseFromString(html, "text/html");
  appWindow.document.body.innerHTML = parsed.body.innerHTML;
  const bridge = {
    connect,
    on: (name: string, handler: (params: object) => void) => {
      handlers[name] = handler;
    },
  };
  // Run the view's actual script against an isolated iframe. The CDN import
  // is routed to a stub so each test decides how the renderer loads.
  const script = parsed.querySelectorAll("script")[1].textContent!;
  expect(script).toContain("import(MERMAID_URL)");
  new Function(
    "orcheoBridge",
    "window",
    "document",
    "loadModule",
    script.replace("import(MERMAID_URL)", "loadModule(MERMAID_URL)"),
  )(bridge, appWindow, appWindow.document, loadModule);
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  connect = vi.fn().mockResolvedValue({});
  loadModule = vi.fn().mockResolvedValue({ default: mermaid });
  mermaid.render.mockResolvedValue({ svg: '<svg id="graph"></svg>' });
});

afterEach(() => {
  frame.remove();
  vi.useRealTimers();
});

describe("workflow diagram MCP App", () => {
  it("renders the diagram the host delivers", async () => {
    mount();
    expect(status()).toBe("Loading diagram…");

    show();

    await vi.waitFor(() => expect(status()).toBe(""));
    expect(element("graph")).not.toBeNull();
    expect(element("name").textContent).toBe("Billing");
    expect(element("version").textContent).toBe("v3");
    expect(mermaid.render).toHaveBeenCalledWith(expect.any(String), graph);
  });

  it("connects without waiting for the renderer to load", () => {
    loadModule.mockReturnValue(new Promise(() => {}));

    mount();

    expect(connect).toHaveBeenCalledExactlyOnceWith("orcheo-workflow-diagram");
  });

  it("draws in the dark theme the host reports", async () => {
    connect.mockResolvedValue({ hostContext: { theme: "dark" } });
    mount();
    await vi.waitFor(() => expect(connect).toHaveBeenCalled());

    show();

    await vi.waitFor(() =>
      expect(mermaid.initialize).toHaveBeenLastCalledWith(
        expect.objectContaining({ theme: "dark" }),
      ),
    );
  });

  it("shows the source when the renderer cannot load", async () => {
    loadModule.mockRejectedValue(new Error("blocked"));
    mount();

    show();

    await vi.waitFor(() =>
      expect(status()).toContain("renderer could not be loaded"),
    );
    expect(element("diagram").textContent).toBe(graph);
  });

  it("shows the source when the renderer never finishes loading", async () => {
    loadModule.mockReturnValue(new Promise(() => {}));
    mount();
    show();
    expect(status()).toBe("Loading diagram…");

    vi.advanceTimersByTime(10000);

    await vi.waitFor(() =>
      expect(status()).toContain("renderer could not be loaded"),
    );
    expect(element("diagram").textContent).toBe(graph);
  });

  it("explains a view that never receives its diagram", () => {
    mount();

    vi.advanceTimersByTime(8000);

    expect(status()).toContain("never received its data");
    expect(status()).toContain("show it again");
    expect(element("status").className).toBe("error");
  });

  it("keeps a delivered diagram when the stall timer would have fired", async () => {
    mount();
    show();
    await vi.waitFor(() => expect(status()).toBe(""));

    vi.advanceTimersByTime(8000);

    expect(status()).toBe("");
  });

  it("explains a cancelled tool call", () => {
    mount();

    handlers["ui/notifications/tool-cancelled"]({ reason: "user cancelled" });

    expect(status()).toContain("was cancelled");
    expect(element("status").className).toBe("error");
  });

  it("explains a host that refuses the handshake", async () => {
    connect.mockRejectedValue(new Error("Invalid params"));

    mount();

    await vi.waitFor(() =>
      expect(status()).toContain("could not reach the app host"),
    );
    expect(element("status").className).toBe("error");
  });
});
