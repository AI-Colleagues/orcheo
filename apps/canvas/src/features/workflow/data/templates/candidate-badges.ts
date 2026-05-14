import type { Workflow } from "../workflow-types";
import type { WorkflowTemplateDefinition } from "./template-definition";
import { PYTHON_AGENT_TEMPLATE, PYTHON_AGENT_WORKFLOW } from "./python-agent";

interface CandidateBadgeSpec {
  id: string;
  name: string;
  handle: string;
  subtitle: string;
  description: string;
  emoji: string;
}

export interface CandidateBadgeDefinition extends CandidateBadgeSpec {
  workflow: Workflow;
  templateDefinition: WorkflowTemplateDefinition;
}

const cloneBaseWorkflow = (spec: CandidateBadgeSpec): Workflow => {
  const baseVersions = PYTHON_AGENT_WORKFLOW.versions ?? [];

  return {
    ...PYTHON_AGENT_WORKFLOW,
    id: spec.id,
    handle: spec.handle,
    name: spec.name,
    description: spec.description,
    owner: {
      id: `${spec.id}-owner`,
      name: spec.name,
      avatar: "",
    },
    tags: [...PYTHON_AGENT_WORKFLOW.tags],
    nodes: PYTHON_AGENT_WORKFLOW.nodes.map((node) => ({
      ...node,
      position: { ...node.position },
      data: { ...node.data },
    })),
    edges: PYTHON_AGENT_WORKFLOW.edges.map((edge) => ({
      ...edge,
      style: edge.style ? { ...edge.style } : undefined,
    })),
    versions: baseVersions.map((version, index) => ({
      ...version,
      id: `${spec.id}-v${index + 1}`,
    })),
  };
};

const CANDIDATE_BADGE_SPECS: CandidateBadgeSpec[] = [
  {
    id: "template-insight-analyst",
    name: "Insight Analyst",
    handle: "insight-analyst",
    subtitle: "AI Insights & Analytics",
    description:
      "Detects themes from text data using advanced thematic coding frameworks, then synthesizes findings into comprehensive, actionable insight reports.",
    emoji: "👨‍🎓",
  },
  {
    id: "template-marketing-specialist",
    name: "Marketing Specialist",
    handle: "marketing-specialist",
    subtitle: "AI Content & Campaigns",
    description:
      "Creates engaging content for websites, blogs, emails, and social media platforms - crafted to captivate target audiences and power integrated marketing campaigns.",
    emoji: "🧑‍💼",
  },
  {
    id: "template-market-intelligence-analyst",
    name: "Market Intelligence Analyst",
    handle: "market-intelligence",
    subtitle: "AI Competitive Intelligence",
    description:
      "Gathers, analyzes, and interprets data on competitors, customers, and market trends to deliver actionable intelligence for strategic decision-making.",
    emoji: "🕵️",
  },
  {
    id: "template-market-research-interviewer",
    name: "Market Research Interviewer",
    handle: "market-research",
    subtitle: "AI Consumer Research",
    description:
      "Conducts structured online interviews to collect data on consumer opinions, behaviors, and preferences - helping organizations develop informed and effective strategies.",
    emoji: "🙋",
  },
];

const CANDIDATE_BADGES = CANDIDATE_BADGE_SPECS.map((spec) => {
  const workflow = cloneBaseWorkflow(spec);
  const templateDefinition: WorkflowTemplateDefinition = {
    workflow,
    script: PYTHON_AGENT_TEMPLATE.script,
    entrypoint: PYTHON_AGENT_TEMPLATE.entrypoint,
    runnableConfig: PYTHON_AGENT_TEMPLATE.runnableConfig,
    notes: PYTHON_AGENT_TEMPLATE.notes,
  };

  return {
    ...spec,
    workflow,
    templateDefinition,
  };
});

export const CANDIDATE_WORKFLOWS: Workflow[] = CANDIDATE_BADGES.map(
  (badge) => badge.workflow,
);

export const CANDIDATE_TEMPLATE_DEFINITIONS: WorkflowTemplateDefinition[] =
  CANDIDATE_BADGES.map((badge) => badge.templateDefinition);

export const getCandidateBadgeDefinition = (
  workflowId: string,
): CandidateBadgeDefinition | undefined => {
  return CANDIDATE_BADGES.find((badge) => badge.workflow.id === workflowId);
};
