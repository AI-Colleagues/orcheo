import { vi } from "vitest";

import { handleCredentialRequest } from "@/testing/mocks/backend/credentials";
import {
  emptyResponse,
  jsonResponse,
} from "@/testing/mocks/backend/request-utils";
import {
  handleWorkflowRequest,
  seedWorkflows,
} from "@/testing/mocks/backend/workflows";

export const setupMockBackendFetch = () => {
  seedWorkflows();

  const originalFetch =
    typeof globalThis.fetch === "function"
      ? globalThis.fetch.bind(globalThis)
      : undefined;

  const backendFetch = vi.fn(
    async (
      input: Parameters<typeof fetch>[0],
      init?: Parameters<typeof fetch>[1],
    ) => {
      const clonedInit = init ? { ...init } : undefined;
      if (clonedInit) {
        delete clonedInit.signal;
      }
      const request =
        input instanceof Request ? input : new Request(input, clonedInit);

      const url = new URL(request.url, "http://localhost:2025");

      if (!url.pathname.startsWith("/api/")) {
        if (originalFetch) {
          return originalFetch(input as Parameters<typeof fetch>[0], init);
        }
        return emptyResponse({ status: 501 });
      }

      if (url.pathname.startsWith("/api/credentials")) {
        return handleCredentialRequest(request, url);
      }

      if (url.pathname.startsWith("/api/workflows")) {
        return handleWorkflowRequest(request, url);
      }

      return jsonResponse({ detail: "Unhandled mock fetch" }, { status: 404 });
    },
  );

  globalThis.fetch = backendFetch as typeof fetch;
  return backendFetch;
};
