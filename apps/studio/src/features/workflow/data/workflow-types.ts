import type { WorkflowCandidateSource } from "@features/workflow/lib/workflow-storage.types";

export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    label: string;
    description?: string;
    status?: "idle" | "running" | "success" | "error";
    isDisabled?: boolean;
    backendType?: string;
    [key: string]: unknown;
  };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  label?: string;
  type?: string;
  animated?: boolean;
  style?: Record<string, unknown>;
}

export interface WorkflowMermaidPreviewVersion {
  id: string;
  mermaid?: string | null;
  templateId?: string;
  candidateSource?: WorkflowCandidateSource;
}

export interface Workflow {
  id: string;
  handle?: string;
  teamId?: string | null;
  /**
   * Group slug for candidate colleagues, derived from the first path segment
   * of the colleague-candidates directory (e.g. "news_desk"). Null when the
   * candidate lives directly under `colleagues/` and is therefore independent.
   */
  candidateGroup?: string | null;
  name: string;
  description?: string;
  avatarEmoji?: string | null;
  uploadError?: {
    message: string;
    occurredAt: string;
  };
  draftAccess?: "personal" | "authenticated" | "workspace";
  createdAt: string;
  updatedAt: string;
  sourceExample?: string;
  owner: {
    id: string;
    name: string;
    avatar: string;
  };
  tags: string[];
  lastRun?: {
    status: "success" | "error" | "running" | "idle";
    timestamp: string;
    duration: number;
  };
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  versions?: WorkflowMermaidPreviewVersion[];
}
