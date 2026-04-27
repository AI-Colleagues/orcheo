import { describe, expect, it } from "vitest";

import {
  buildConfigurableSchema,
  toFormData,
  toWorkflowConfig,
} from "@features/workflow/pages/workflow-canvas/components/workflow-config-sheet.utils";

describe("workflow-config-sheet utils", () => {
  it("preserves upload-time runnable config fields", () => {
    const formData = {
      configurable: { tenant: "acme", region: "us-east-1" },
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
      configurable: { tenant: "acme", region: "us-east-1" },
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
});
