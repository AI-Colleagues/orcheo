import type { RJSFSchema } from "@rjsf/utils";

import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const toPositiveInteger = (value: unknown): number | undefined => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  const integer = Math.floor(value);
  return integer > 0 ? integer : undefined;
};

const inferArrayItemsSchema = (value: unknown[]): RJSFSchema => {
  if (value.length === 0) {
    return { type: "string" };
  }

  const itemSchemas = value.map((item) => inferSchemaFromValue(item));
  const firstSchema = JSON.stringify(itemSchemas[0]);
  const hasSingleItemShape = itemSchemas.every(
    (itemSchema) => JSON.stringify(itemSchema) === firstSchema,
  );

  return hasSingleItemShape ? itemSchemas[0] : {};
};

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

export const buildConfigurableSchema = (
  configurable: unknown,
  schemaDefinitions?: Record<string, RJSFSchema>,
): RJSFSchema["properties"] => {
  if (!isRecord(configurable)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(configurable).map(([key, value]) => {
      const inferredSchema = inferSchemaFromValue(value);
      const declaredSchema = schemaDefinitions?.[key];
      
      return [
        key,
        declaredSchema ? mergeSchemas(inferredSchema, declaredSchema) : inferredSchema,
      ];
    }),
  );
};

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
