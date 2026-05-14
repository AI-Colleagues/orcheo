import { describe, expect, it } from "vitest";

import {
  ID_1_PORTRAIT_RATIO,
  WORKFLOW_NODE_CARD_ASPECT_RATIO,
  WORKFLOW_NODE_CARD_HEIGHT_PX,
  WORKFLOW_NODE_CARD_SIZE_CLASSNAME,
  WORKFLOW_NODE_CARD_WIDTH_PX,
} from "./node-card-size";

describe("workflow node card size", () => {
  it("matches the ID-1 portrait ratio closely", () => {
    expect(WORKFLOW_NODE_CARD_ASPECT_RATIO).toBeCloseTo(
      ID_1_PORTRAIT_RATIO,
      3,
    );
  });

  it("uses a portrait-oriented size class", () => {
    expect(WORKFLOW_NODE_CARD_SIZE_CLASSNAME).toBe("h-[192px] w-[121px]");
    expect(WORKFLOW_NODE_CARD_WIDTH_PX).toBeLessThan(
      WORKFLOW_NODE_CARD_HEIGHT_PX,
    );
  });
});
