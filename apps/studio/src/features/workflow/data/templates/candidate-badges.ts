import { seededAvatarId } from "@/assets/avatars";
import type { Workflow } from "../workflow-types";
import type { StoredWorkflow } from "../../lib/workflow-storage.types";
import type {
  WorkflowTemplateDefinition,
  WorkflowTemplateMetadata,
} from "./template-definition";

export interface CandidateBadgeSpec {
  id: string;
  /** Original candidate id from the server (used by the onboard endpoint). */
  candidateId: string;
  name: string;
  handle: string;
  subtitle?: string;
  description?: string;
  /** Avatar ID ("avatar-01" … "avatar-21"), "random", or absent for a seeded random avatar. */
  avatar?: string;
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
    typeof raw.template_version === "string" ? raw.template_version : undefined,
  minOrcheoVersion:
    typeof raw.min_orcheo_version === "string"
      ? raw.min_orcheo_version
      : undefined,
});

const buildCandidateWorkflow = (
  spec: CandidateBadgeSpec,
  existingHandles: Set<string>,
): Workflow => {
  // Preserve original handle if no conflict exists, otherwise let backend generate unique handle
  const preserveHandle =
    spec.handle && !existingHandles.has(spec.handle) ? spec.handle : undefined;

  const avatarId =
    spec.avatar && spec.avatar !== "random"
      ? spec.avatar
      : seededAvatarId(spec.id);

  return {
    id: spec.id,
    handle: preserveHandle,
    name: spec.name,
    description: spec.description,
    avatarEmoji: avatarId,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    owner: {
      id: `${spec.id}-owner`,
      name: spec.name,
      avatar: "",
    },
    tags: ["template"],
    nodes: [],
    edges: [],
    versions: spec.mermaid
      ? [{ id: `${spec.id}-v1`, mermaid: spec.mermaid }]
      : [],
  };
};

const buildCandidateBadge = (
  spec: CandidateBadgeSpec,
  existingHandles: Set<string>,
): CandidateBadgeDefinition => {
  const workflow = buildCandidateWorkflow(spec, existingHandles);
  const templateDefinition: WorkflowTemplateDefinition = {
    workflow,
    script: "",
    entrypoint: undefined,
    runnableConfig: null,
    notes: spec.notes ?? "",
    metadata: spec.rawMetadata ? mapRawMetadata(spec.rawMetadata) : undefined,
  };
  return { ...spec, workflow, templateDefinition };
};

// Candidates are sourced at runtime from the colleague-candidates repository
// (via the backend /api/candidates endpoint), so this registry is populated
// after fetch rather than at module load.
let candidateBadges: CandidateBadgeDefinition[] = [];

export const setCandidateBadges = (
  specs: CandidateBadgeSpec[],
  existingWorkflows: StoredWorkflow[] = [],
): void => {
  // Create set of existing handles to check for conflicts
  const existingHandles = new Set<string>(
    existingWorkflows
      .map((w) => w.handle)
      .filter((handle): handle is string => Boolean(handle)),
  );

  candidateBadges = specs.map((spec) =>
    buildCandidateBadge(spec, existingHandles),
  );
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
