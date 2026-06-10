import type React from "react";
import type { UseChatKitOptions } from "@openai/chatkit-react";

export interface ChatParticipant {
  id: string;
  name: string;
  avatar?: string;
}

export interface InitialMessage {
  id: string;
  content: string;
  sender: {
    id: string;
    name: string;
    avatar?: string;
    isAI?: boolean;
  };
  timestamp: Date | string;
  status?: "sending" | "sent" | "delivered" | "read" | "error";
}

export interface ChatInterfaceProps {
  title?: string;
  initialMessages?: InitialMessage[];
  className?: string;
  isMinimizable?: boolean;
  isClosable?: boolean;
  position?:
    | "bottom-right"
    | "bottom-left"
    | "top-right"
    | "top-left"
    | "center";
  triggerButton?: React.ReactNode;
  user: ChatParticipant;
  ai: ChatParticipant;
  backendBaseUrl?: string;
  workflowId?: string | null;
  sessionPayload?: Record<string, unknown>;
  getClientSecret?: (currentSecret: string | null) => Promise<string>;
  chatkitOptions?: Partial<UseChatKitOptions>;
  onResponseStart?: () => void;
  onResponseEnd?: () => void;
  onThreadChange?: (threadId: string | null) => void;
  onLog?: (payload: Record<string, unknown>) => void;
}
