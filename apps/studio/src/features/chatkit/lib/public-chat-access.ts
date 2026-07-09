import type { PublicChatHttpError } from "@features/chatkit/lib/chatkit-client";

export const getPublicChatAccessErrorMessage = (
  error: Pick<PublicChatHttpError, "status" | "code">,
): string => {
  if (error.code === "chatkit.auth.workspace_mismatch") {
    return "Your account is signed in, but it is not a member of this workflow workspace. Switch workspaces or ask the owner to add you.";
  }
  if (error.status === 403) {
    return "You do not have permission to use this workflow. Ask the owner to confirm your workspace access.";
  }
  return "You do not have access to this workflow yet. Ask the owner to confirm it is still published.";
};
