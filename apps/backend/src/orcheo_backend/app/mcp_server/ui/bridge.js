// Minimal MCP Apps (io.modelcontextprotocol/ui, 2026-01-26) client.
// JSON-RPC 2.0 over postMessage with the host that embeds this view.
const orcheoBridge = (() => {
  const PROTOCOL_VERSION = "2026-01-26";
  const pending = new Map();
  const listeners = new Map();
  let nextId = 1;

  const post = (message) =>
    window.parent.postMessage({ jsonrpc: "2.0", ...message }, "*");

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0") return;
    if (!message.method && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message || "Request failed"));
      else resolve(message.result);
      return;
    }
    if (!message.method) return;
    if (message.id !== undefined) {
      // Host requests (e.g. ui/resource-teardown) only need acknowledging.
      post({ id: message.id, result: {} });
    }
    for (const handler of listeners.get(message.method) || []) {
      handler(message.params || {});
    }
  });

  const request = (method, params) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      post({ id, method, params });
    });

  const notify = (method, params) => post({ method, params });

  const on = (method, handler) => {
    listeners.set(method, [...(listeners.get(method) || []), handler]);
  };

  const applyTheme = (context) => {
    if (context && context.theme) {
      document.documentElement.dataset.theme = context.theme;
    }
  };

  const reportSize = () =>
    notify("ui/notifications/size-changed", {
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    });

  on("ui/notifications/host-context-changed", applyTheme);

  const connect = async (name) => {
    const result = await request("ui/initialize", {
      protocolVersion: PROTOCOL_VERSION,
      clientInfo: { name, version: "1.0.0" },
      appCapabilities: {},
    });
    applyTheme(result && result.hostContext);
    notify("ui/notifications/initialized", {});
    new ResizeObserver(reportSize).observe(document.body);
    reportSize();
    return result;
  };

  const callTool = async (name, args) => {
    const result = await request("tools/call", { name, arguments: args });
    if (result && result.isError) {
      const text = (result.content || []).map((item) => item.text || "").join(" ");
      throw new Error(text || "The request failed.");
    }
    return result;
  };

  const tellModel = (text) =>
    request("ui/update-model-context", {
      content: [{ type: "text", text }],
    }).catch(() => undefined);

  return { callTool, connect, notify, on, request, tellModel };
})();
