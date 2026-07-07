import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";

import { CheckboxWidget } from "./rjsf-input-widgets";

afterEach(() => {
  cleanup();
});

const createProps = (overrides: Partial<WidgetProps> = {}): WidgetProps =>
  ({
    id: "root_configurable_dry_run",
    label: "Dry Run",
    value: false,
    onChange: vi.fn(),
    disabled: false,
    readonly: false,
    required: false,
    schema: {
      type: "boolean",
      description:
        "When true, the report is produced and sent but processed items are NOT marked read.",
    },
    uiSchema: {},
    registry: {} as WidgetProps["registry"],
    options: {},
    ...overrides,
  }) as WidgetProps;

describe("CheckboxWidget", () => {
  it("shows the schema description in a tooltip on hover", async () => {
    const user = userEvent.setup();

    render(<CheckboxWidget {...createProps()} />);

    const helpButton = screen.getByRole("button", { name: "Dry Run help" });
    await user.hover(helpButton);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "When true, the report is produced and sent but processed items are NOT marked read.",
    );
  });

  it("renders no help button when the schema has no description", () => {
    render(
      <CheckboxWidget {...createProps({ schema: { type: "boolean" } })} />,
    );

    expect(
      screen.queryByRole("button", { name: "Dry Run help" }),
    ).not.toBeInTheDocument();
  });
});
