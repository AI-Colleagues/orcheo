import { RegistryWidgetsType } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { basicWidgets } from "./rjsf-basic-widgets";
import { cronWidgets } from "./rjsf-cron-widget";
import { customTemplates } from "./rjsf-templates";

export const customWidgets = {
  ...basicWidgets,
  ...cronWidgets,
} satisfies RegistryWidgetsType;

export { customTemplates, validator };
