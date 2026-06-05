import { afterEach, describe, expect, it } from "vitest";

import { getWorkflowTemplateDefinition } from "@features/workflow/data/workflow-data";
import { setCandidateBadges } from "./candidate-badges";
import {
  assertWorkflowTemplateCompatibility,
  type WorkflowTemplateDefinition,
} from "./template-definition";

afterEach(() => {
  setCandidateBadges([]);
});

describe("template compatibility", () => {
  it("resolves candidate templates from the runtime registry", () => {
    setCandidateBadges([
      {
        id: "template-telegram-private-listener",
        handle: "telegram-private-listener",
        name: "Telegram Private Listener",
        script: "from orcheo.nodes.telegram import TelegramBotListenerNode\n",
        rawMetadata: { validated_provider_api: "telegram-bot-api" },
      },
    ]);

    const template = getWorkflowTemplateDefinition(
      "template-telegram-private-listener",
    );
    expect(template).toBeDefined();
    expect(template!.workflow.name).toBe("Telegram Private Listener");
    expect(() => assertWorkflowTemplateCompatibility(template!)).not.toThrow();
  });

  it("returns undefined for a deleted static template with no candidateBadge", () => {
    const template = getWorkflowTemplateDefinition(
      "template-qq-private-listener",
    );
    expect(template).toBeUndefined();
  });

  it("rejects templates when provider or reply-node contracts drift", () => {
    const staleTemplate: WorkflowTemplateDefinition = {
      workflow: {
        id: "template-stale",
        name: "Stale Template",
        description: "Outdated template.",
        createdAt: "2026-03-11T12:00:00Z",
        updatedAt: "2026-03-11T12:00:00Z",
        owner: { id: "team-templates", name: "Orcheo Templates", avatar: "" },
        tags: ["template"],
        nodes: [],
        edges: [],
        versions: [],
      },
      script: "print('stale')",
      notes: "stale",
      metadata: {
        templateVersion: "1.0.0",
        validatedProviderApi: "qq-bot-api-v3",
        replyNodeContracts: ["MessageQQNode@2"],
      },
    };

    expect(() => assertWorkflowTemplateCompatibility(staleTemplate)).toThrow(
      "requires revalidation",
    );
  });
});
