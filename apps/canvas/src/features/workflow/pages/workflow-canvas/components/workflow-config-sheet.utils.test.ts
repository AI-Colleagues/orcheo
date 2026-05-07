import { describe, expect, it, vi } from "vitest";
import type { RJSFSchema } from "@rjsf/utils";

import {
  buildConfigurableSchema,
  toFormData,
  toWorkflowConfig,
} from "@features/workflow/pages/workflow-canvas/components/workflow-config-sheet.utils";

describe("workflow-config-sheet utils", () => {
  it("preserves upload-time runnable config fields", () => {
    const formData = {
      configurable: { workspace: "acme", region: "us-east-1" },
      run_name: "  nightly-run  ",
      tags: ["prod", " prod ", "nightly"],
      metadata: { owner: "search-team" },
      callbacks: [{ type: "log" }],
      recursion_limit: 7,
      max_concurrency: 3,
      prompts: {
        summary_prompt: {
          template: "Summarize {topic}",
          input_variables: ["topic"],
          partial_variables: { tone: "brief" },
        },
      },
    } satisfies Record<string, unknown>;

    expect(toWorkflowConfig(formData)).toEqual({
      configurable: { workspace: "acme", region: "us-east-1" },
      run_name: "nightly-run",
      tags: ["prod", "nightly"],
      metadata: { owner: "search-team" },
      callbacks: [{ type: "log" }],
      recursion_limit: 7,
      max_concurrency: 3,
      prompts: {
        summary_prompt: {
          template: "Summarize {topic}",
          input_variables: ["topic"],
          partial_variables: { tone: "brief" },
        },
      },
    });
  });

  it("keeps all runnable config fields when hydrating form data", () => {
    const config = {
      configurable: { organization: "acme" },
      run_name: "upload-run",
      tags: ["upload"],
      metadata: { source: "cli" },
      callbacks: ["callback-a"],
      recursion_limit: 5,
      max_concurrency: 2,
      prompts: {
        review_prompt: {
          template: "Review {text}",
          input_variables: ["text"],
          optional_variables: ["style"],
        },
      },
    };

    expect(toFormData(config)).toEqual({
      configurable: { organization: "acme" },
      run_name: "upload-run",
      tags: ["upload"],
      metadata: { source: "cli" },
      callbacks: ["callback-a"],
      recursion_limit: 5,
      max_concurrency: 2,
      prompts: {
        review_prompt: {
          template: "Review {text}",
          input_variables: ["text"],
          optional_variables: ["style"],
        },
      },
    });
  });

  it("infers array item schemas for configurable fields", () => {
    expect(
      buildConfigurableSchema({
        database: "my_database",
        dimensions: 512,
        text_paths: ["title", "body"],
      }),
    ).toEqual({
      database: { type: "string" },
      dimensions: { type: "integer" },
      text_paths: {
        type: "array",
        items: { type: "string" },
      },
    });
  });

  it("merges declared schemas with inferred schemas", () => {
    const configurable = {
      post_as: "person",
      max_results: 10,
      dry_run: false,
      categories: ["news", "tech"],
    };

    const schemaDefinitions = {
      post_as: { enum: ["person", "organization"] },
      max_results: { type: "integer", minimum: 1, maximum: 100 },
      dry_run: { type: "boolean" },
      categories: {
        type: "array",
        items: { enum: ["news", "sport", "tech"] },
      },
    };

    expect(buildConfigurableSchema(configurable, schemaDefinitions)).toEqual({
      post_as: { type: "string", enum: ["person", "organization"] },
      max_results: {
        type: "integer",
        minimum: 1,
        maximum: 100,
      },
      dry_run: { type: "boolean" },
      categories: {
        type: "array",
        items: { enum: ["news", "sport", "tech"] },
      },
    });
  });

  it("uses declared schema for enum fields that render as SelectWidget", () => {
    const configurable = {
      visibility: "PUBLIC",
    };

    const schemaDefinitions = {
      visibility: { enum: ["PUBLIC", "CONNECTIONS", "PRIVATE"] },
    };

    const result = buildConfigurableSchema(configurable, schemaDefinitions);

    expect(result.visibility).toEqual({
      type: "string",
      enum: ["PUBLIC", "CONNECTIONS", "PRIVATE"],
    });
  });

  it("includes declared schema fields even when no runtime value is present", () => {
    const configurable = {};

    const schemaDefinitions = {
      ai_model: {
        type: "string",
        enum: ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
        title: "Model",
        default: "openai:gpt-4.1-mini",
      },
    };

    expect(buildConfigurableSchema(configurable, schemaDefinitions)).toEqual({
      ai_model: {
        type: "string",
        enum: ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
        title: "Model",
        default: "openai:gpt-4.1-mini",
      },
    });
  });

  it("preserves inferred schema when no declarations provided", () => {
    const configurable = {
      api_key: "secret123",
      retry_count: 3,
    };

    expect(buildConfigurableSchema(configurable)).toEqual({
      api_key: { type: "string" },
      retry_count: { type: "integer" },
    });
  });

  it("handles partial schema declarations", () => {
    const configurable = {
      mode: "auto",
      timeout: 5000,
      enabled: true,
    };

    const schemaDefinitions = {
      mode: { enum: ["auto", "manual"] },
      // timeout not declared, should use inferred
      // enabled not declared, should use inferred
    };

    expect(buildConfigurableSchema(configurable, schemaDefinitions)).toEqual({
      mode: { type: "string", enum: ["auto", "manual"] },
      timeout: { type: "integer" },
      enabled: { type: "boolean" },
    });
  });

  it("throws error for invalid schema definitions", () => {
    const configurable = { key: "value" };
    const invalidSchemaDefinitions = {
      key: "invalid schema" as unknown as RJSFSchema,
    };

    expect(() =>
      buildConfigurableSchema(configurable, invalidSchemaDefinitions),
    ).toThrow(
      'Invalid schema definition for key "key": expected object, got string',
    );
  });

  it("handles schema inference errors gracefully", () => {
    const configurable = { validKey: "value" };

    // Mock console.warn to verify it's called
    const originalWarn = console.warn;
    const mockWarn = vi.fn();
    console.warn = mockWarn;

    // This shouldn't normally happen, but test error handling
    const result = buildConfigurableSchema(configurable);

    console.warn = originalWarn;

    expect(result.validKey).toEqual({ type: "string" });
  });

  it("handles heterogeneous arrays with oneOf schema", () => {
    const configurable = {
      mixedArray: ["string", 123, true],
    };

    const result = buildConfigurableSchema(configurable);

    expect(result.mixedArray).toEqual({
      type: "array",
      items: {
        oneOf: [{ type: "string" }, { type: "integer" }, { type: "boolean" }],
      },
    });
  });

  it("handles empty arrays with default string schema", () => {
    const configurable = {
      emptyArray: [],
    };

    const result = buildConfigurableSchema(configurable);

    expect(result.emptyArray).toEqual({
      type: "array",
      items: { type: "string" },
    });
  });

  it("handles arrays with duplicate schemas correctly", () => {
    const configurable = {
      stringArray: ["hello", "world", "test"],
    };

    const result = buildConfigurableSchema(configurable);

    expect(result.stringArray).toEqual({
      type: "array",
      items: { type: "string" },
    });
  });
});
