import type { ModelOption, StartScreenPrompt } from "@openai/chatkit";
import type {
  ChatKitStartScreenPrompt as WorkflowChatKitStartScreenPrompt,
  ChatKitSupportedModel as WorkflowChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";

export const buildStartScreenPrompts = (
  workflowName: string,
  configuredPrompts?: WorkflowChatKitStartScreenPrompt[] | null,
): StartScreenPrompt[] => {
  if (configuredPrompts !== undefined && configuredPrompts !== null) {
    return configuredPrompts.map((prompt) => ({
      label: prompt.label,
      prompt: prompt.prompt,
      ...(prompt.icon ? { icon: prompt.icon } : {}),
    }));
  }

  return [
    {
      label: "What can you do?",
      prompt: `What can ${workflowName} help with?`,
      icon: "circle-question",
    },
    {
      label: "Introduce yourself",
      prompt: "My name is ...",
      icon: "book-open",
    },
    {
      label: "Latest results",
      prompt: `Summarize the latest run for ${workflowName}.`,
      icon: "search",
    },
    {
      label: "Switch theme",
      prompt: "Change the theme to dark mode",
      icon: "sparkle",
    },
  ];
};

export const buildModelOptions = (
  configuredModels?: WorkflowChatKitSupportedModel[] | null,
): ModelOption[] | undefined => {
  if (configuredModels === undefined || configuredModels === null) {
    return undefined;
  }

  const enabledModels = configuredModels.filter((model) => !model.disabled);
  const hasExplicitDefault = enabledModels.some((model) => model.default);
  let assignedFallbackDefault = false;
  const normalized = configuredModels.map((model) => {
    const shouldAssignFallbackDefault =
      !hasExplicitDefault && !model.disabled && !assignedFallbackDefault;
    if (shouldAssignFallbackDefault) {
      assignedFallbackDefault = true;
    }
    return {
      id: model.id,
      label: model.label?.trim() || model.id,
      ...(model.description ? { description: model.description } : {}),
      ...(model.disabled ? { disabled: true } : {}),
      ...(model.default || shouldAssignFallbackDefault
        ? { default: true }
        : {}),
    };
  });
  return normalized.length > 0 ? normalized : undefined;
};
