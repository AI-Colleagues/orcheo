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
}

export interface Workflow {
  id: string;
  handle?: string;
  name: string;
  description?: string;
  avatarEmoji?: string | null;
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
