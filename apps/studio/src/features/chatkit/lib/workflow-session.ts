import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";

interface WorkflowSessionResponse {
  client_secret?: string;
  clientSecret?: string;
  expires_at?: string;
  expiresAt?: string;
}

export interface WorkflowChatSession {
  clientSecret: string;
  expiresAt: number | null;
}

const parseErrorDetail = async (response: Response): Promise<string> => {
  try {
    const payload = (await response.json()) as Record<string, unknown>;
    if (typeof payload?.detail === "object" && payload.detail !== null) {
      const detail = payload.detail as Record<string, unknown>;
      const message =
        typeof detail.message === "string" ? detail.message : null;
      if (message) {
        return message;
      }
    }
    if (typeof payload.message === "string") {
      return payload.message;
    }
  } catch {
    // Ignore JSON parse errors and fall through to status text.
  }
  return response.statusText || "ChatKit session request failed.";
};

export async function requestWorkflowChatSession(
  workflowId: string,
  backendBaseUrl?: string | null,
): Promise<WorkflowChatSession> {
  const url = buildBackendHttpUrl(
    `/api/workflows/${workflowId}/chatkit/session`,
    backendBaseUrl ?? undefined,
  );
  const response = await authFetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    credentials: "include",
  });

  if (!response.ok) {
    const message = await parseErrorDetail(response);
    throw new Error(message);
  }

  const payload = (await response.json()) as WorkflowSessionResponse;
  const secret = payload.client_secret ?? payload.clientSecret;
  if (!secret) {
    throw new Error("ChatKit session response missing client secret.");
  }

  const expiresRaw = payload.expires_at ?? payload.expiresAt;
  const expiresAt = expiresRaw ? new Date(expiresRaw).getTime() : null;

  return {
    clientSecret: secret,
    expiresAt,
  };
}
