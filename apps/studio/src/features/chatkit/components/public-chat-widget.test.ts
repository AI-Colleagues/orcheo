import { describe, expect, it } from "vitest";
import {
  buildModelOptions,
  buildStartScreenPrompts,
} from "./public-chat-config";

describe("buildStartScreenPrompts", () => {
  it("uses configured workflow prompts when present", () => {
    expect(
      buildStartScreenPrompts("Demo workflow", [
        {
          label: "Summarize the latest run",
          prompt: "Summarize the latest run for me.",
          icon: "search",
        },
        {
          label: "What changed?",
          prompt: "What changed since yesterday?",
        },
      ]),
    ).toEqual([
      {
        label: "Summarize the latest run",
        prompt: "Summarize the latest run for me.",
        icon: "search",
      },
      {
        label: "What changed?",
        prompt: "What changed since yesterday?",
      },
    ]);
  });

  it("falls back to built-in prompts when no workflow prompts are set", () => {
    expect(buildStartScreenPrompts("Demo workflow")).toEqual([
      {
        label: "What can you do?",
        prompt: "What can Demo workflow help with?",
        icon: "circle-question",
      },
      {
        label: "Introduce yourself",
        prompt: "My name is ...",
        icon: "book-open",
      },
      {
        label: "Latest results",
        prompt: "Summarize the latest run for Demo workflow.",
        icon: "search",
      },
      {
        label: "Switch theme",
        prompt: "Change the theme to dark mode",
        icon: "sparkle",
      },
    ]);
  });

  it("uses configured workflow models when present", () => {
    expect(
      buildModelOptions([
        {
          id: "openai:gpt-5",
          label: "GPT-5",
          description: "Best quality",
          default: true,
        },
        {
          id: "openai:gpt-5-mini",
          label: "GPT-5 Mini",
        },
      ]),
    ).toEqual([
      {
        id: "openai:gpt-5",
        label: "GPT-5",
        description: "Best quality",
        default: true,
      },
      {
        id: "openai:gpt-5-mini",
        label: "GPT-5 Mini",
      },
    ]);
  });

  it("hides the model picker when no workflow models are set", () => {
    expect(buildModelOptions()).toBeUndefined();
  });

  it("hides the model picker when the configured model list is empty", () => {
    expect(buildModelOptions([])).toBeUndefined();
  });

  it("ignores disabled defaults when assigning fallback default", () => {
    expect(
      buildModelOptions([
        {
          id: "openai:gpt-5",
          label: "GPT-5",
          default: true,
          disabled: true,
        },
        {
          id: "openai:gpt-5-mini",
          label: "GPT-5 Mini",
        },
      ]),
    ).toEqual([
      {
        id: "openai:gpt-5",
        label: "GPT-5",
        disabled: true,
        default: true,
      },
      {
        id: "openai:gpt-5-mini",
        label: "GPT-5 Mini",
        default: true,
      },
    ]);
  });
});
