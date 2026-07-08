import { describe, expect, it } from "vitest";

import { parseWorkflowFrontmatter } from "./workflow-frontmatter";

describe("parseWorkflowFrontmatter", () => {
  it("extracts upload metadata from an orcheo frontmatter block", () => {
    const source = `# /// orcheo
# name = "Lead Router"
# handle = "lead-router"
# description = "Routes inbound leads."
# entrypoint = "build_graph"
# ///

print("hi")
`;

    expect(parseWorkflowFrontmatter(source)).toEqual({
      name: "Lead Router",
      handle: "lead-router",
      description: "Routes inbound leads.",
      entrypoint: "build_graph",
    });
  });

  it("returns an empty object when the script has no orcheo block", () => {
    expect(parseWorkflowFrontmatter("print('hi')")).toEqual({});
  });

  it("ignores non-string frontmatter values", () => {
    const source = `# /// orcheo
# name = ["bad"]
# handle = "valid-handle"
# ///
`;

    expect(parseWorkflowFrontmatter(source)).toEqual({
      handle: "valid-handle",
    });
  });
});
