// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import { useBrowserContext } from "./use-browser-context";

let fetchSpy: MockInstance;
let warnSpy: MockInstance;

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response());
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function HookHarness() {
  const { setPageContext } = useBrowserContext();
  return (
    <div>
      <button
        data-testid="set-gallery"
        onClick={() => setPageContext({ page: "gallery" })}
      />
      <button
        data-testid="set-canvas"
        onClick={() =>
          setPageContext({
            page: "canvas",
            workflowId: "wf-1",
            workflowName: "Test",
          })
        }
      />
    </div>
  );
}

describe("useBrowserContext", () => {
  it("fires POST on setPageContext", async () => {
    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:3333/context",
      expect.objectContaining({ method: "POST" }),
    );

    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    );
    expect(body.page).toBe("gallery");
    expect(body.session_id).toBeTruthy();
  });

  it("generates stable session_id from sessionStorage", async () => {
    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });

    const firstBody = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    );
    const sessionId = firstBody.session_id;

    expect(sessionStorage.getItem("orcheo_browser_session_id")).toBe(sessionId);
  });

  it("posts workflow_id and workflow_name for canvas context", async () => {
    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-canvas").click();
    });

    const call = fetchSpy.mock.calls.find((c) => {
      const body = JSON.parse((c as [string, RequestInit])[1].body as string);
      return body.page === "canvas";
    }) as [string, RequestInit] | undefined;

    expect(call).toBeDefined();
    const body = JSON.parse(call![1].body as string);
    expect(body.workflow_id).toBe("wf-1");
    expect(body.workflow_name).toBe("Test");
  });

  it("starts heartbeat on mount when tab is visible", async () => {
    vi.useFakeTimers();

    render(<HookHarness />);
    fetchSpy.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });

    expect(fetchSpy).toHaveBeenCalled();
  });

  it("warns once and disables sync after repeated fetch failure", async () => {
    vi.useFakeTimers();
    fetchSpy.mockRejectedValue(new Error("Connection refused"));

    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(warnSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      screen.getByTestId("set-canvas").click();
      window.dispatchEvent(new Event("focus"));
      vi.advanceTimersByTime(5_000);
    });

    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("includes focused flag based on document.hasFocus", async () => {
    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });

    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    );
    expect(typeof body.focused).toBe("boolean");
  });

  it("fires POST on focus event", async () => {
    render(<HookHarness />);

    // Set a context first so there's something to send
    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });
    fetchSpy.mockClear();

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(fetchSpy).toHaveBeenCalled();
    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    );
    expect(body.focused).toBe(true);
  });

  it("fires POST on blur event", async () => {
    render(<HookHarness />);

    await act(async () => {
      screen.getByTestId("set-gallery").click();
    });
    fetchSpy.mockClear();

    await act(async () => {
      window.dispatchEvent(new Event("blur"));
    });

    expect(fetchSpy).toHaveBeenCalled();
    const body = JSON.parse(
      (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as string,
    );
    expect(body.focused).toBe(false);
  });
});
