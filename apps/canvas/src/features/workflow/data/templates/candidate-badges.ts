import type { Workflow } from "../workflow-types";
import type {
  WorkflowTemplateDefinition,
  WorkflowTemplateMetadata,
} from "./template-definition";

export interface CandidateBadgeSpec {
  id: string;
  name: string;
  handle: string;
  subtitle?: string;
  description?: string;
  emoji?: string;
  script?: string;
  config?: Record<string, unknown> | null;
  entrypoint?: string | null;
  notes?: string | null;
  mermaid?: string | null;
  /** Raw snake_case metadata dict from the colleague-candidates frontmatter. */
  rawMetadata?: Record<string, unknown> | null;
}

export interface CandidateBadgeDefinition extends CandidateBadgeSpec {
  workflow: Workflow;
  templateDefinition: WorkflowTemplateDefinition;
}

const toStringArray = (value: unknown): string[] | undefined => {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const strings = value.filter((v): v is string => typeof v === "string");
  return strings.length > 0 ? strings : undefined;
};

const mapRawMetadata = (
  raw: Record<string, unknown>,
): WorkflowTemplateMetadata => ({
  requiredPlugins: toStringArray(raw.required_plugins),
  validatedProviderApi:
    typeof raw.validated_provider_api === "string"
      ? raw.validated_provider_api
      : undefined,
  replyNodeContracts: toStringArray(raw.reply_node_contracts),
  templateVersion:
    typeof raw.template_version === "string"
      ? raw.template_version
      : undefined,
  minOrcheoVersion:
    typeof raw.min_orcheo_version === "string"
      ? raw.min_orcheo_version
      : undefined,
});

const buildCandidateWorkflow = (spec: CandidateBadgeSpec): Workflow => ({
  id: spec.id,
  handle: spec.handle,
  name: spec.name,
  description: spec.description,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  owner: {
    id: `${spec.id}-owner`,
    name: spec.name,
    avatar: spec.emoji ?? "",
  },
  tags: ["template"],
  nodes: [],
  edges: [],
  versions: spec.mermaid
    ? [{ id: `${spec.id}-v1`, mermaid: spec.mermaid }]
    : [],
});

const buildCandidateBadge = (
  spec: CandidateBadgeSpec,
): CandidateBadgeDefinition => {
  const workflow = buildCandidateWorkflow(spec);
  const templateDefinition: WorkflowTemplateDefinition = {
    workflow,
    script: spec.script ?? "",
    entrypoint: spec.entrypoint ?? undefined,
    runnableConfig: spec.config ?? null,
    notes: spec.notes ?? "",
    metadata: spec.rawMetadata ? mapRawMetadata(spec.rawMetadata) : undefined,
  };
  return { ...spec, workflow, templateDefinition };
};

// Candidates are sourced at runtime from the colleague-candidates repository
// (via the backend /api/candidates endpoint), so this registry is populated
// after fetch rather than at module load.
let candidateBadges: CandidateBadgeDefinition[] = [];

export const setCandidateBadges = (specs: CandidateBadgeSpec[]): void => {
  candidateBadges = specs.map(buildCandidateBadge);
};

export const getCandidateWorkflows = (): Workflow[] =>
  candidateBadges.map((badge) => badge.workflow);

export const getCandidateBadgeDefinition = (
  workflowId: string,
): CandidateBadgeDefinition | undefined =>
  candidateBadges.find((badge) => badge.workflow.id === workflowId);

export const getCandidateTemplateDefinition = (
  templateId: string,
): WorkflowTemplateDefinition | undefined =>
  getCandidateBadgeDefinition(templateId)?.templateDefinition;
