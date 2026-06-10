import Form from "@rjsf/core";
import type { RegistryFieldsType, RJSFSchema, UiSchema } from "@rjsf/utils";

import {
  customTemplates,
  customWidgets,
  validator,
} from "@features/workflow/components/panels/rjsf-theme";

interface SchemaConfigFormProps {
  schema: RJSFSchema;
  uiSchema?: UiSchema;
  formData: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  fields?: RegistryFieldsType;
}

export function SchemaConfigForm({
  schema,
  uiSchema,
  formData,
  onChange,
  fields,
}: SchemaConfigFormProps) {
  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      formData={formData}
      onChange={(event) => {
        onChange((event.formData as Record<string, unknown>) ?? {});
      }}
      validator={validator}
      widgets={customWidgets}
      fields={fields}
      templates={customTemplates}
    >
      <div className="hidden" />
    </Form>
  );
}
