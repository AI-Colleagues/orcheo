import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { FieldTemplateProps } from "@rjsf/utils";

import { FieldTemplate } from "./rjsf-templates";

afterEach(() => {
  cleanup();
});

const createProps = (
  overrides: Partial<FieldTemplateProps> = {},
): FieldTemplateProps =>
  ({
    id: "root_configurable_database",
    label: "Database",
    children: <input id="root_configurable_database" />,
    errors: null,
    help: null,
    description: "MongoDB database name used by the workflow.",
    hidden: false,
    required: false,
    displayLabel: true,
    rawErrors: [],
    classNames: "",
    formContext: {},
    onChange: undefined,
    registry: {} as FieldTemplateProps["registry"],
    schema: {
      type: "string",
    },
    uiSchema: {},
    ...overrides,
  }) as FieldTemplateProps;

describe("FieldTemplate", () => {
  it("shows schema descriptions in a tooltip on hover", async () => {
    const user = userEvent.setup();

    render(<FieldTemplate {...createProps()} />);

    const helpButton = screen.getByRole("button", {
      name: "Database help",
    });
    await user.hover(helpButton);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "MongoDB database name used by the workflow.",
    );
  });
});
