import { describe, expect, it } from "vitest";

import { getWorkflowTemplateDefinition } from "@features/workflow/data/workflow-data";
import {
  assertWorkflowTemplateCompatibility,
  type WorkflowTemplateDefinition,
} from "./template-definition";

describe("template compatibility", () => {
  it("accepts the shipped private listener templates", () => {
    const qqTemplate = getWorkflowTemplateDefinition(
      "template-qq-private-listener",
    );
    const sharedTemplate = getWorkflowTemplateDefinition(
      "template-private-bot-shared-listener",
    );
    const pluginTemplate = getWorkflowTemplateDefinition(
      "template-wecom-lark-shared-listener",
    );
    const wechatTemplate = getWorkflowTemplateDefinition(
      "template-wechat-private-listener",
    );

    expect(qqTemplate).toBeDefined();
    expect(sharedTemplate).toBeDefined();
    expect(pluginTemplate).toBeDefined();
    expect(wechatTemplate).toBeDefined();
    expect(() =>
      assertWorkflowTemplateCompatibility(
        qqTemplate as WorkflowTemplateDefinition,
      ),
    ).not.toThrow();
    expect(() =>
      assertWorkflowTemplateCompatibility(
        sharedTemplate as WorkflowTemplateDefinition,
      ),
    ).not.toThrow();
    expect(() =>
      assertWorkflowTemplateCompatibility(
        pluginTemplate as WorkflowTemplateDefinition,
      ),
    ).not.toThrow();
    expect(() =>
      assertWorkflowTemplateCompatibility(
        wechatTemplate as WorkflowTemplateDefinition,
      ),
    ).not.toThrow();
  });

  it("includes the chatkit widgets template in the registry", () => {
    const chatkitTemplate = getWorkflowTemplateDefinition(
      "template-chatkit-widgets",
    );
    expect(chatkitTemplate).toBeDefined();
    expect(chatkitTemplate!.workflow.name).toBe("ChatKit Widgets Agent");
    expect(chatkitTemplate!.script).toContain("mcp-chatkit-widget");
    expect(chatkitTemplate!.workflow.tags).toContain("chatkit");
  });

  it("includes the Claude Code, Codex, and Gemini external-agent templates", () => {
    const claudeTemplate = getWorkflowTemplateDefinition(
      "template-claude-code-agent",
    );
    const codexTemplate = getWorkflowTemplateDefinition("template-codex-agent");
    const geminiTemplate = getWorkflowTemplateDefinition(
      "template-gemini-agent",
    );

    expect(claudeTemplate).toBeDefined();
    expect(codexTemplate).toBeDefined();
    expect(geminiTemplate).toBeDefined();
    expect(claudeTemplate!.workflow.name).toBe("Claude Code Agent");
    expect(codexTemplate!.workflow.name).toBe("Codex Agent");
    expect(geminiTemplate!.workflow.name).toBe("Gemini Agent");
    expect(claudeTemplate!.runnableConfig?.configurable).toEqual({
      working_directory: "/workspace/agents",
    });
    expect(codexTemplate!.runnableConfig?.configurable).toEqual({
      working_directory: "/workspace/agents",
    });
    expect(geminiTemplate!.runnableConfig?.configurable).toEqual({
      working_directory: "/workspace/agents",
    });
    expect(geminiTemplate!.script).toContain(
      "from orcheo.nodes.ai.external.gemini import GeminiNode",
    );
  });

  it("rejects templates when provider or reply-node contracts drift", () => {
    const staleTemplate: WorkflowTemplateDefinition = {
      workflow: {
        id: "template-stale",
        name: "Stale Template",
        description: "Outdated template.",
        createdAt: "2026-03-11T12:00:00Z",
        updatedAt: "2026-03-11T12:00:00Z",
        owner: "Shaojie Jiang",
        tags: ["template"],
        nodes: [],
        edges: [],
        versions: [],
      },
      script: "print('stale')",
      notes: "stale",
      metadata: {
        templateVersion: "1.0.0",
        minOrcheoVersion: "0.1.0",
        validatedProviderApi: "qq-bot-api-v3",
        validationDate: "2026-03-11",
        owner: "Shaojie Jiang",
        acceptanceCriteria: [],
        revalidationTriggers: [],
        replyNodeContracts: ["MessageQQNode@2"],
      },
    };

    expect(() => assertWorkflowTemplateCompatibility(staleTemplate)).toThrow(
      "requires revalidation",
    );
  });
});
