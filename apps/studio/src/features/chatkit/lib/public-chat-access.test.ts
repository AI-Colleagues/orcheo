import { describe, expect, it } from "vitest";
import { getPublicChatAccessErrorMessage } from "./public-chat-access";

describe("getPublicChatAccessErrorMessage", () => {
  it("surfaces workspace mismatch errors specifically", () => {
    expect(
      getPublicChatAccessErrorMessage({
        status: 403,
        code: "chatkit.auth.workspace_mismatch",
      }),
    ).toContain("not a member of this workflow workspace");
  });

  it("distinguishes generic forbidden errors from unauthenticated errors", () => {
    expect(getPublicChatAccessErrorMessage({ status: 403 })).toContain(
      "permission",
    );
    expect(getPublicChatAccessErrorMessage({ status: 401 })).toContain(
      "still published",
    );
  });
});
