import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useChatInterfaceOptions } from "./chat-interface-options";

describe("useChatInterfaceOptions", () => {
  it("adds attachment support for workflow-scoped chat surfaces", () => {
    const { result } = renderHook(() =>
      useChatInterfaceOptions({
        backendBaseUrl: "http://localhost:2025",
        workflowId: "wf-123",
        sessionPayload: {},
        title: "Insight Analyst",
        user: { id: "user-1", name: "User", avatar: "" },
        ai: { id: "ai-1", name: "AI", avatar: "" },
        initialMessages: [],
      } as never),
    );

    expect(result.current.composer?.attachments).toEqual(
      expect.objectContaining({
        enabled: true,
        maxSize: 5 * 1024 * 1024,
        maxCount: 10,
      }),
    );
    expect(result.current.composer?.attachments?.accept).toMatchObject({
      "application/pdf": [".pdf"],
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
    });
    expect(result.current.api?.uploadStrategy).toEqual(
      expect.objectContaining({
        type: "direct",
      }),
    );
  });
});
