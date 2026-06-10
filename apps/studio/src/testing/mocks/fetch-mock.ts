import { beforeEach, vi } from "vitest";

export const createFetchMockHarness = () => {
  const mockFetch = vi.fn<Parameters<typeof fetch>, ReturnType<typeof fetch>>();

  const queueResponses = (responses: Response[]) => {
    const queue = [...responses];
    mockFetch.mockImplementation(() => {
      const next = queue.shift();
      if (!next) {
        throw new Error("No more mocked responses available");
      }

      return Promise.resolve(next);
    });
  };

  const getFetchMock = () => mockFetch;

  const setupFetchMock = () => {
    beforeEach(() => {
      mockFetch.mockReset();
      globalThis.fetch = mockFetch as unknown as typeof fetch;
    });
  };

  return { getFetchMock, queueResponses, setupFetchMock };
};
