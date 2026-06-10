import type { ReactNode } from "react";
import { vi } from "vitest";

vi.mock("@openai/chatkit-react", () => ({
  ChatKit: () => null,
  ChatKitProvider: ({ children }: { children?: ReactNode }) => children ?? null,
  useChatKit: () => ({
    control: {
      setInstance: vi.fn(),
      options: {},
      handlers: {},
    },
    focusComposer: vi.fn(),
    setThreadId: vi.fn(),
    sendUserMessage: vi.fn(),
    setComposerValue: vi.fn(),
    fetchUpdates: vi.fn(),
    sendCustomAction: vi.fn(),
    ref: { current: null },
  }),
}));
