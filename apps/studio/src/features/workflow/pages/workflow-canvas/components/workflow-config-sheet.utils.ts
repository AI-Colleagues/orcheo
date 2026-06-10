import type { RJSFSchema } from "@rjsf/utils";

import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Validates if an unknown value is a valid RJSFSchema object.
 * @param schema - The value to validate
 * @returns True if the value is a valid schema object
 */
const isValidSchema = (schema: unknown): schema is RJSFSchema => {
  return (
    typeof schema === "object" && schema !== null && !Array.isArray(schema)
  );
};

const toPositiveInteger = (value: unknown): number | undefined => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  const integer = Math.floor(value);
  return integer > 0 ? integer : undefined;
};

/**
 * Infers schema for array items, handling both homogeneous and heterogeneous arrays.
 * @param value - The array to analyze
 * @returns Schema for array items
 */
const inferArrayItemsSchema = (value: unknown[]): RJSFSchema => {
  if (value.length === 0) {
    return { type: "string" };
  }

  const itemSchemas = value.map((item) => inferSchemaFromValue(item));
  const firstSchema = JSON.stringify(itemSchemas[0]);
  const hasSingleItemShape = itemSchemas.every(
    (itemSchema) => JSON.stringify(itemSchema) === firstSchema,
  );

  if (hasSingleItemShape) {
    return itemSchemas[0];
  }

  // For heterogeneous arrays, create a union type of all observed schemas
  const uniqueSchemas = itemSchemas.filter((schema, index, array) => {
    const schemaStr = JSON.stringify(schema);
    return array.findIndex((s) => JSON.stringify(s) === schemaStr) === index;
  });

  // If we have multiple types, use oneOf for better widget support
  return uniqueSchemas.length > 1
    ? { oneOf: uniqueSchemas }
    : uniqueSchemas[0] || {};
};

/**
 * Infers a JSON Schema from a given value.
 * @param value - The value to analyze
 * @returns RJSFSchema representing the inferred type and structure
 */
export const inferSchemaFromValue = (value: unknown): RJSFSchema => {
  if (Array.isArray(value)) {
    return {
      type: "array",
      items: inferArrayItemsSchema(value),
    };
  }

  if (isRecord(value)) {
    return {
      type: "object",
      properties: Object.fromEntries(
        Object.entries(value).map(([key, itemValue]) => [
          key,
          inferSchemaFromValue(itemValue),
        ]),
      ),
      additionalProperties: true,
      default: {},
    };
  }

  if (typeof value === "string") {
    return { type: "string" };
  }

  if (typeof value === "number") {
    return { type: Number.isInteger(value) ? "integer" : "number" };
  }

  if (typeof value === "boolean") {
    return { type: "boolean" };
  }

  if (value === null) {
    return { type: "null" };
  }

  return {};
};

/**
 * Merges an inferred schema with a declared schema, prioritizing declared properties.
 * @param inferredSchema - Schema derived from the actual data
 * @param declaredSchema - Explicitly declared schema from definitions
 * @returns Merged schema with declared properties taking precedence
 */
const mergeSchemas = (
  inferredSchema: RJSFSchema,
  declaredSchema: RJSFSchema,
): RJSFSchema => {
  // Declared schema takes priority over inferred schema
  const merged = { ...inferredSchema, ...declaredSchema };

  // For objects, merge properties recursively
  if (
    inferredSchema.type === "object" &&
    declaredSchema.type === "object" &&
    inferredSchema.properties &&
    declaredSchema.properties
  ) {
    merged.properties = {
      ...inferredSchema.properties,
      ...declaredSchema.properties,
    };
  }

  return merged;
};

/**
 * Builds a complete schema for configurable properties by combining inferred and declared schemas.
 * @param configurable - The configurable data object
 * @param schemaDefinitions - Optional declared schema definitions
 * @returns Schema properties for the configurable object
 * @throws Error if schemaDefinitions contains invalid schema objects
 */
export const buildConfigurableSchema = (
  configurable: unknown,
  schemaDefinitions?: Record<string, RJSFSchema>,
): RJSFSchema["properties"] => {
  if (!isRecord(configurable)) {
    if (!schemaDefinitions) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(schemaDefinitions).map(([key, schema]) => [key, schema]),
    );
  }

  // Validate schema definitions if provided
  if (schemaDefinitions) {
    for (const [key, schema] of Object.entries(schemaDefinitions)) {
      if (!isValidSchema(schema)) {
        throw new Error(
          `Invalid schema definition for key "${key}": expected object, got ${typeof schema}`,
        );
      }
    }
  }

  const keys = new Set([
    ...Object.keys(configurable),
    ...Object.keys(schemaDefinitions ?? {}),
  ]);

  return Object.fromEntries(
    Array.from(keys).map((key) => {
      const value = configurable[key];
      try {
        const inferredSchema = inferSchemaFromValue(value);
        const declaredSchema = schemaDefinitions?.[key];

        return [
          key,
          declaredSchema
            ? mergeSchemas(inferredSchema, declaredSchema)
            : inferredSchema,
        ];
      } catch (error) {
        // If schema inference fails, fall back to a basic string schema
        console.warn(
          `Failed to infer schema for key "${key}": ${error instanceof Error ? error.message : String(error)}`,
        );
        return [key, { type: "string" }];
      }
    }),
  );
};

/**
 * Converts form data to a WorkflowRunnableConfig object.
 * @param formData - Form data from the UI
 * @returns Validated workflow configuration or null if empty
 */
export const toWorkflowConfig = (
  formData: Record<string, unknown>,
): WorkflowRunnableConfig | null => {
  const next: WorkflowRunnableConfig = {};

  if (
    isRecord(formData.configurable) &&
    Object.keys(formData.configurable).length > 0
  ) {
    next.configurable = formData.configurable;
  }

  if (typeof formData.run_name === "string") {
    const runName = formData.run_name.trim();
    if (runName.length > 0) {
      next.run_name = runName;
    }
  }

  if (Array.isArray(formData.tags)) {
    const tags = formData.tags
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim())
      .filter(
        (item, index, array) =>
          item.length > 0 && array.indexOf(item) === index,
      );
    if (tags.length > 0) {
      next.tags = tags;
    }
  }

  if (
    isRecord(formData.metadata) &&
    Object.keys(formData.metadata).length > 0
  ) {
    next.metadata = formData.metadata;
  }

  if (Array.isArray(formData.callbacks) && formData.callbacks.length > 0) {
    next.callbacks = formData.callbacks;
  }

  const recursionLimit = toPositiveInteger(formData.recursion_limit);
  if (recursionLimit) {
    next.recursion_limit = recursionLimit;
  }

  const maxConcurrency = toPositiveInteger(formData.max_concurrency);
  if (maxConcurrency) {
    next.max_concurrency = maxConcurrency;
  }

  if (isRecord(formData.prompts) && Object.keys(formData.prompts).length > 0) {
    next.prompts = formData.prompts;
  }

  return Object.keys(next).length > 0 ? next : null;
};

/**
 * Converts a WorkflowRunnableConfig to form data for the UI.
 * @param config - Workflow configuration object or null
 * @returns Form data object with default values
 */
export const toFormData = (
  config: WorkflowRunnableConfig | null,
): Record<string, unknown> => ({
  configurable: config?.configurable ?? {},
  run_name: config?.run_name ?? "",
  tags: config?.tags ?? [],
  metadata: config?.metadata ?? {},
  callbacks: config?.callbacks ?? [],
  recursion_limit: config?.recursion_limit,
  max_concurrency: config?.max_concurrency,
  prompts: config?.prompts ?? {},
});
