import { Workflow } from "../workflow-types";
import {
  assertWorkflowTemplateCompatibility,
  type WorkflowTemplateDefinition,
} from "./template-definition";
import { getCandidateTemplateDefinition } from "./candidate-badges";
import { VIBE_AGENT_TEMPLATE } from "./vibe-agent";

export const SAMPLE_WORKFLOWS: Workflow[] = [];

export const GALLERY_TEMPLATE_WORKFLOWS: Workflow[] = [];

export const WORKFLOW_TEMPLATE_DEFINITIONS: WorkflowTemplateDefinition[] = [
  VIBE_AGENT_TEMPLATE,
];

const TEMPLATE_BY_ID = new Map(
  WORKFLOW_TEMPLATE_DEFINITIONS.map((definition) => [
    definition.workflow.id,
    definition,
  ]),
);

export const getWorkflowTemplateDefinition = (
  templateId: string,
): WorkflowTemplateDefinition | undefined => {
  return (
    TEMPLATE_BY_ID.get(templateId) ?? getCandidateTemplateDefinition(templateId)
  );
};

export { assertWorkflowTemplateCompatibility };
