/**
 * Aggregates the basic widget sets to keep the theme entry tidy.
 */

import { RegistryWidgetsType } from "@rjsf/utils";
import { primitiveWidgets } from "./rjsf-input-widgets";
import { textWidgets } from "./rjsf-text-widgets";

export const basicWidgets = {
  ...textWidgets,
  ...primitiveWidgets,
} satisfies Pick<
  RegistryWidgetsType,
  | "TextWidget"
  | "TextareaWidget"
  | "NumberWidget"
  | "CheckboxWidget"
  | "SelectWidget"
>;

export { primitiveWidgets, textWidgets };
