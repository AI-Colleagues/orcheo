import { RegistryWidgetsType } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { basicWidgets } from "./rjsf-basic-widgets";
import { customTemplates } from "./rjsf-templates";

export const customWidgets = {
  ...basicWidgets,
} satisfies RegistryWidgetsType;

export { customTemplates, validator };
