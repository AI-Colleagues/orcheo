import { RJSFSchema } from "@rjsf/utils";

import { baseNodeSchema } from "@features/workflow/lib/node-schemas/base";

export const aiNodeSchemas: Record<string, RJSFSchema> = {
  Agent: {
    type: "object",
    properties: {
      ...baseNodeSchema.properties,
      modelSettings: {
        type: "object",
        title: "Model Settings",
        description: "Configuration for the AI model",
        properties: {
          model: {
            type: "string",
            title: "Model",
            description: "Model identifier (e.g., gpt-4, claude-3-opus)",
          },
          temperature: {
            type: "number",
            title: "Temperature",
            description: "Controls randomness in responses",
            minimum: 0,
            maximum: 2,
            default: 0.7,
          },
          maxTokens: {
            type: "integer",
            title: "Max Tokens",
            description: "Maximum number of tokens to generate",
            minimum: 1,
          },
        },
      },
      systemPrompt: {
        type: "string",
        title: "System Prompt",
        description: "System prompt for the agent",
      },
      checkpointer: {
        type: "string",
        title: "Checkpointer",
        description: "Checkpointer used to save the agent's state",
        enum: ["postgres"],
      },
      structuredOutput: {
        type: "object",
        title: "Structured Output",
        description: "Configuration for structured output",
        properties: {
          schemaType: {
            type: "string",
            title: "Schema Type",
            enum: ["json_schema", "json_dict", "pydantic", "typed_dict"],
          },
          schemaStr: {
            type: "string",
            title: "Schema Definition",
            description: "The schema definition as a string",
          },
        },
      },
    },
    required: ["modelSettings"],
  },

  PythonCode: {
    type: "object",
    properties: {
      ...baseNodeSchema.properties,
      code: {
        type: "string",
        title: "Python Code",
        description: "Python code to execute",
        default: "def run(state, config):\n    return {}\n",
      },
    },
    required: ["code"],
  },
};
