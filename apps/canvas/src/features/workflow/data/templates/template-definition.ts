import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";
import type { Workflow } from "../workflow-types";

export interface WorkflowTemplateMetadata {
  templateVersion?: string;
  minOrcheoVersion?: string;
  validatedProviderApi?: string;
  validationDate?: string;
  owner?: string;
  acceptanceCriteria?: string[];
  revalidationTriggers?: string[];
  replyNodeContracts?: string[];
  requiredPlugins?: string[];
}

export interface WorkflowTemplateDefinition {
  workflow: Workflow;
  script: string;
  entrypoint?: string;
  runnableConfig?: WorkflowRunnableConfig | null;
  notes: string;
  metadata?: WorkflowTemplateMetadata;
}

const CURRENT_PROVIDER_APIS = new Set([
  "telegram-bot-api",
  "discord-gateway-v10",
  "qq-bot-api-v2",
  "private-bot-listener-suite-2026-03-11",
  "wecom-lark-listener-plugin-suite-2026-03-16",
  "openclaw-wechat-plugin-2026-03-22",
]);

const CURRENT_REPLY_NODE_CONTRACTS = new Set([
  "MessageTelegramNode@1",
  "MessageDiscordNode@1",
  "MessageQQNode@1",
  "WechatReplyNode@1",
]);

export const getWorkflowTemplateCompatibilityIssues = (
  definition: WorkflowTemplateDefinition,
): string[] => {
  const metadata = definition.metadata;
  if (!metadata) {
    return [];
  }

  const issues: string[] = [];
  if (
    metadata.validatedProviderApi &&
    !CURRENT_PROVIDER_APIS.has(metadata.validatedProviderApi)
  ) {
    issues.push(
      `provider API '${metadata.validatedProviderApi}' requires revalidation`,
    );
  }

  for (const contract of metadata.replyNodeContracts ?? []) {
    if (!CURRENT_REPLY_NODE_CONTRACTS.has(contract)) {
      issues.push(`reply node contract '${contract}' requires revalidation`);
    }
  }
  return issues;
};

export const assertWorkflowTemplateCompatibility = (
  definition: WorkflowTemplateDefinition,
): void => {
  const issues = getWorkflowTemplateCompatibilityIssues(definition);
  if (issues.length === 0) {
    return;
  }
  throw new Error(
    `Template '${definition.workflow.id}' is out of date: ${issues.join("; ")}`,
  );
};
