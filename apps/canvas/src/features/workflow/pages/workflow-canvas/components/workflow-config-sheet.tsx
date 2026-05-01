import { useEffect, useMemo, useState } from "react";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";

import { Button } from "@/design-system/ui/button";
import { ScrollArea } from "@/design-system/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/design-system/ui/sheet";
import { SchemaConfigForm } from "@features/workflow/components/forms/schema-config-form";
import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";
import {
  buildConfigurableSchema,
  toFormData,
  toWorkflowConfig,
} from "@features/workflow/pages/workflow-canvas/components/workflow-config-sheet.utils";

const baseWorkflowConfigSchema: RJSFSchema = {
  type: "object",
  properties: {
    configurable: {
      type: "object",
      title: "Configurable",
      additionalProperties: true,
      default: {},
    },
    run_name: {
      type: "string",
      title: "Run name",
    },
    tags: {
      type: "array",
      title: "Tags",
      items: {
        type: "string",
      },
    },
    metadata: {
      type: "object",
      title: "Metadata",
      additionalProperties: true,
      default: {},
    },
    callbacks: {
      type: "array",
      title: "Callbacks",
      items: {
        type: ["object", "array", "string", "number", "integer", "boolean"],
      },
    },
    recursion_limit: {
      type: "integer",
      title: "Recursion limit",
      minimum: 1,
    },
    max_concurrency: {
      type: "integer",
      title: "Max concurrency",
      minimum: 1,
    },
    prompts: {
      type: "object",
      title: "Prompts",
      additionalProperties: {
        type: "object",
        properties: {
          template: {
            type: "string",
            title: "Template",
          },
          input_variables: {
            type: "array",
            title: "Input variables",
            items: {
              type: "string",
            },
          },
          optional_variables: {
            type: "array",
            title: "Optional variables",
            items: {
              type: "string",
            },
          },
          partial_variables: {
            type: "object",
            title: "Partial variables",
            additionalProperties: true,
          },
        },
        required: ["template", "input_variables"],
      },
      default: {},
    },
  },
};

const workflowConfigUiSchema: UiSchema = {
  run_name: {
    "ui:placeholder": "Optional run label",
  },
  tags: {
    "ui:options": {
      orderable: false,
    },
  },
};

interface WorkflowConfigSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialConfig: WorkflowRunnableConfig | null;
  onSave: (nextConfig: WorkflowRunnableConfig | null) => Promise<void>;
  configurableSchemas?: Record<string, RJSFSchema>;
}

export function WorkflowConfigSheet({
  open,
  onOpenChange,
  initialConfig,
  onSave,
  configurableSchemas,
}: WorkflowConfigSheetProps) {
  const [formData, setFormData] = useState<Record<string, unknown>>(
    toFormData(initialConfig),
  );
  const [isSaving, setIsSaving] = useState(false);

  const workflowConfigSchema = useMemo<RJSFSchema>(() => {
    const configurableSchema =
      baseWorkflowConfigSchema.properties?.configurable;

    return {
      ...baseWorkflowConfigSchema,
      properties: {
        ...baseWorkflowConfigSchema.properties,
        configurable: {
          ...configurableSchema,
          properties: buildConfigurableSchema(
            formData.configurable,
            configurableSchemas,
          ),
        },
      },
    };
  }, [formData.configurable, configurableSchemas]);

  useEffect(() => {
    if (open) {
      setFormData(toFormData(initialConfig));
    }
  }, [initialConfig, open]);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave(toWorkflowConfig(formData));
      onOpenChange(false);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full p-0 sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-4">
          <SheetTitle>Workflow config</SheetTitle>
          <SheetDescription>
            Configure runnable options saved with this workflow version.
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="h-[calc(100vh-9.5rem)]">
          <div className="p-6">
            <SchemaConfigForm
              schema={workflowConfigSchema}
              uiSchema={workflowConfigUiSchema}
              formData={formData}
              onChange={setFormData}
            />
          </div>
        </ScrollArea>

        <SheetFooter className="border-t px-6 py-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void handleSave()} disabled={isSaving}>
            {isSaving ? "Saving..." : "Save config"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
