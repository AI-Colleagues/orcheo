/* eslint-disable react-refresh/only-export-components */
/**
 * Field templates that control layout/styling of form sections.
 */

import React from "react";
import {
  ArrayFieldTemplateProps,
  FieldTemplateProps,
  ObjectFieldTemplateProps,
} from "@rjsf/utils";
import { Label } from "@/design-system/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { Button } from "@/design-system/ui/button";
import { HelpCircle, Plus, X } from "lucide-react";

/**
 * Custom Field Template
 */
function FieldTemplate(props: FieldTemplateProps) {
  const {
    id,
    label,
    children,
    errors,
    help,
    description,
    hidden,
    required,
    displayLabel,
  } = props;

  if (hidden) {
    return <div className="hidden">{children}</div>;
  }

  return (
    <div className="grid gap-2 mb-4">
      {displayLabel && label && (
        <div className="flex items-center gap-1.5">
          <Label htmlFor={id}>
            {label}
            {required && <span className="text-destructive ml-1">*</span>}
          </Label>
          {description && (
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${label} help`}
                    className="inline-flex items-center justify-center rounded-full h-4 w-4 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <HelpCircle className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right" className="max-w-[300px]">
                  <p className="text-xs">{description}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      )}
      {children}
      {errors && <div className="text-xs text-destructive">{errors}</div>}
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}

/**
 * Custom Object Field Template
 */
function ObjectFieldTemplate(props: ObjectFieldTemplateProps) {
  const { title, description, properties } = props;

  return (
    <div className="space-y-4">
      {title && <h4 className="font-medium text-sm">{title}</h4>}
      {description && (
        <p className="text-xs text-muted-foreground mb-2">{description}</p>
      )}
      <div className="space-y-3">
        {properties.map((element) => (
          <div key={element.name}>{element.content}</div>
        ))}
      </div>
    </div>
  );
}

/**
 * Custom Array Field Template
 */
function ArrayFieldTemplate(props: ArrayFieldTemplateProps) {
  const { title, items, canAdd, onAddClick } = props;

  return (
    <div className="space-y-3">
      {title && <h4 className="font-medium text-sm">{title}</h4>}
      <div className="space-y-3">
        {items.map((element) => (
          <div
            key={element.key}
            className="rounded-md border border-border bg-muted/30 p-3 space-y-3"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">
                Item {element.index + 1}
              </span>
              {element.hasRemove && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground"
                  onClick={element.onDropIndexClick(element.index)}
                >
                  <X className="h-3 w-3" />
                </Button>
              )}
            </div>
            {element.children}
          </div>
        ))}
      </div>
      {canAdd && (
        <Button variant="outline" size="sm" onClick={onAddClick}>
          <Plus className="h-3 w-3 mr-1" /> Add Item
        </Button>
      )}
    </div>
  );
}

export const customTemplates = {
  FieldTemplate,
  ObjectFieldTemplate,
  ArrayFieldTemplate,
};

export { FieldTemplate, ObjectFieldTemplate, ArrayFieldTemplate };
