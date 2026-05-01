import { afterEach, describe, expect, it, vi } from "vitest";
import { buildPublicChatFetch } from "./chatkit-client";

const originalFetch = window.fetch;

afterEach(() => {
  window.fetch = originalFetch;
  vi.restoreAllMocks();
});

const createResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("buildPublicChatFetch", () => {
  it("injects workflow id into JSON bodies", async () => {
    const fetchMock = vi.fn(async () => createResponse(200, { ok: true }));
    window.fetch = fetchMock as unknown as typeof window.fetch;

    const handler = buildPublicChatFetch({
      workflowId: "wf-123",
      backendBaseUrl: "http://localhost:8000",
      metadata: { workflow_name: "LangGraph" },
    });

    await handler("http://localhost:8000/api/chatkit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ foo: "bar" }),
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0]!;
    expect(options?.credentials).toBe("include");

    const payload = JSON.parse((options?.body as string) ?? "{}");
    expect(payload.workflow_id).toBe("wf-123");
    expect(payload.foo).toBe("bar");
    expect(payload.metadata.workflow_id).toBe("wf-123");
    expect(payload.metadata.workflow_name).toBe("LangGraph");
  });

  it("injects workflow id into Request bodies", async () => {
    const fetchMock = vi.fn(async () => createResponse(200, { ok: true }));
    window.fetch = fetchMock as unknown as typeof window.fetch;

    const handler = buildPublicChatFetch({
      workflowId: "wf-456",
      backendBaseUrl: "http://localhost:8000",
    });

    await handler(
      new Request("http://localhost:8000/api/chatkit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ foo: "bar" }),
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [input] = fetchMock.mock.calls[0]!;
    expect(input).toBeInstanceOf(Request);
    const payload = JSON.parse(await (input as Request).clone().text());
    expect(payload.workflow_id).toBe("wf-456");
    expect(payload.foo).toBe("bar");
  });

  it("emits structured errors when the backend rejects a request", async () => {
    const fetchMock = vi.fn(async () =>
      createResponse(401, {
        code: "chatkit.auth.oauth_required",
        message: "login first",
      }),
    );
    window.fetch = fetchMock as unknown as typeof window.fetch;

    const onHttpError = vi.fn();
    const handler = buildPublicChatFetch({
      workflowId: "wf-123",
      onHttpError,
    });

    await handler("http://localhost:8000/api/chatkit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    expect(onHttpError).toHaveBeenCalledWith({
      status: 401,
      message: "login first",
      code: "chatkit.auth.oauth_required",
    });
  });

  it("merges existing metadata without overwriting it", async () => {
    const fetchMock = vi.fn(async () => createResponse(200, { ok: true }));
    window.fetch = fetchMock as unknown as typeof window.fetch;

    const handler = buildPublicChatFetch({
      workflowId: "wf-789",
      metadata: { injected: "value" },
    });

    await handler("http://localhost:8000/api/chatkit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        metadata: { existing: "field" },
      }),
    });

    const [, options] = fetchMock.mock.calls[0]!;
    const payload = JSON.parse((options?.body as string) ?? "{}");
    expect(payload.metadata).toMatchObject({
      existing: "field",
      injected: "value",
      workflow_id: "wf-789",
    });
  });

  it("does not inject Authorization headers by default", async () => {
    const fetchMock = vi.fn(async () => createResponse(200, { ok: true }));
    window.fetch = fetchMock as unknown as typeof window.fetch;

    const handler = buildPublicChatFetch({
      workflowId: "wf-222",
    });

    await handler("http://localhost:8000/api/chatkit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    const [, options] = fetchMock.mock.calls[0]!;
    const headers = new Headers(options?.headers ?? {});
    expect(headers.has("Authorization")).toBe(false);
  });
});
