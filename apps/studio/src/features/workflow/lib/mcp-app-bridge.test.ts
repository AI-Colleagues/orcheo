// Exercise the MCP Apps client every backend-hosted view inlines. The view
// tests mock this bridge, so the wire messages it sends are checked here.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

const source = readFileSync(
  resolve(
    __dirname,
    "../../../../../backend/src/orcheo_backend/app/mcp_server/ui/bridge.js",
  ),
  "utf8",
);

type Message = {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: object;
  result?: object;
  error?: { code: number; message: string };
};
type Bridge = { connect: (name: string) => Promise<{ hostContext?: object }> };

let sent: Message[];
let fromHost: (message: Message) => void;
let root: {
  dataset: Record<string, string>;
  scrollWidth: number;
  scrollHeight: number;
};
let bridge: Bridge;

const flush = () => new Promise((settle) => setTimeout(settle, 0));

beforeEach(() => {
  sent = [];
  root = { dataset: {}, scrollWidth: 480, scrollHeight: 320 };
  const parent = { postMessage: (message: Message) => sent.push(message) };
  type Listener = (event: { source: unknown; data: Message }) => void;
  let listener: Listener = () => {};
  const view = {
    parent,
    addEventListener: (_type: string, handler: Listener) => {
      listener = handler;
    },
  };
  fromHost = (data) => listener({ source: parent, data });
  class ResizeObserver {
    observe() {}
  }
  bridge = new Function(
    "window",
    "document",
    "ResizeObserver",
    `${source}\nreturn orcheoBridge;`,
  )(view, { documentElement: root, body: {} }, ResizeObserver);
});

describe("MCP Apps bridge", () => {
  it("opens with the ui/initialize request the MCP Apps spec defines", async () => {
    const connected = bridge.connect("orcheo-test-view");

    // Spec-validating hosts reject the whole handshake for a missing or
    // misnamed field, so the params must match exactly.
    expect(sent).toEqual([
      {
        jsonrpc: "2.0",
        id: 1,
        method: "ui/initialize",
        params: {
          protocolVersion: "2026-01-26",
          appInfo: { name: "orcheo-test-view", version: "1.0.0" },
          appCapabilities: {},
        },
      },
    ]);

    fromHost({
      jsonrpc: "2.0",
      id: 1,
      result: {
        protocolVersion: "2026-01-26",
        hostInfo: { name: "host", version: "1" },
        hostCapabilities: {},
        hostContext: { theme: "dark" },
      },
    });
    await connected;

    expect(root.dataset.theme).toBe("dark");
    expect(sent.slice(1)).toEqual([
      { jsonrpc: "2.0", method: "ui/notifications/initialized", params: {} },
      {
        jsonrpc: "2.0",
        method: "ui/notifications/size-changed",
        params: { width: 480, height: 320 },
      },
    ]);
  });

  it("fails to connect when the host refuses the handshake", async () => {
    const connected = bridge.connect("orcheo-test-view");
    fromHost({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32602, message: "Invalid params for ui/initialize" },
    });

    await expect(connected).rejects.toThrow("Invalid params for ui/initialize");
    await flush();
    expect(sent.map((message) => message.method)).toEqual(["ui/initialize"]);
  });
});
