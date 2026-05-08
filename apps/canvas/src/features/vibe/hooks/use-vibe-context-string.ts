import type { PageContext } from "@/hooks/use-page-context";

const describeCanvasTab = (ctx: PageContext): string | null => {
  const workflowId = ctx.workflowId?.trim();

  switch (ctx.activeTab) {
    case "trace":
      return workflowId
        ? `The user is viewing traces for workflow ${workflowId}.`
        : "The user is viewing traces.";
    case "readiness":
      return workflowId
        ? `The user is viewing readiness for workflow ${workflowId}.`
        : "The user is viewing readiness.";
    case "settings":
      return workflowId
        ? `The user is viewing workflow settings for workflow ${workflowId}.`
        : "The user is viewing workflow settings.";
    case "workflow":
      return workflowId
        ? `The user is viewing the workflow canvas for workflow ${workflowId}.`
        : "The user is viewing the workflow canvas.";
    default:
      return null;
  }
};

export function buildVibeContextString(ctx: PageContext): string {
  const parts: string[] = [];

  switch (ctx.page) {
    case "gallery":
      parts.push("The user is on Canvas Gallery.");
      break;

    case "canvas": {
      const workflowId = ctx.workflowId?.trim();
      if (workflowId) {
        parts.push(`The user is on workflow ${workflowId}.`);
      } else {
        parts.push("The user is creating a new workflow.");
      }
      if (ctx.workflowName?.trim()) {
        parts.push(`The workflow name is ${ctx.workflowName.trim()}.`);
      }
      const tabDescription = describeCanvasTab(ctx);
      if (tabDescription) {
        parts.push(tabDescription);
      }
      break;
    }

    case "remediations":
      parts.push("The user is viewing remediation records.");
      break;

    case "execution":
      if (ctx.executionId) {
        parts.push(
          `The user is viewing execution details for ${ctx.executionId}.`,
        );
      } else {
        parts.push("The user is viewing execution details.");
      }
      break;

    case "settings":
      parts.push("The user is on Settings.");
      break;

    case "profile":
      parts.push("The user is on Profile.");
      break;

    case "help":
      parts.push("The user is on Help & Support.");
      break;

    default:
      parts.push("The user is on Orcheo Canvas.");
      break;
  }

  if (ctx.vaultOpen) {
    parts.push("Credential Vault is opened.");
  }

  return parts.join(" ");
}

export function useVibeContextString(ctx: PageContext): string {
  return buildVibeContextString(ctx);
}
