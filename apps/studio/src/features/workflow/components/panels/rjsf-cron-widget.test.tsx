import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";

import {
  buildCron,
  canonicalCron,
  CronWidget,
  parseCron,
  roundTrips,
} from "./rjsf-cron-widget";

afterEach(cleanup);

const widgetProps = (value: string, onChange = vi.fn()) =>
  ({ id: "cron", value, onChange }) as unknown as WidgetProps;

describe("canonicalCron", () => {
  it("treats empty input as the (representable) default", () => {
    expect(canonicalCron("")).toBe("");
    expect(canonicalCron(undefined)).toBe("");
  });

  it("expands day-of-week ranges to sorted lists", () => {
    expect(canonicalCron("0 0 * * 1-5")).toBe("0 0 * * 1,2,3,4,5");
    expect(canonicalCron("0 0 * * 6-7")).toBe("0 0 * * 0,6");
  });

  it("collapses */1 to the wildcard", () => {
    expect(canonicalCron("*/1 * * * *")).toBe("* * * * *");
  });

  it("rejects malformed and unrepresentable expressions", () => {
    expect(canonicalCron("not a cron")).toBeNull();
    expect(canonicalCron("0 0 * * MON")).toBeNull();
    expect(canonicalCron("0 0 * JAN *")).toBeNull();
    expect(canonicalCron("0 0 * *")).toBeNull(); // too few fields
  });
});

describe("roundTrips", () => {
  it("accepts schedules the visual builder can represent", () => {
    for (const value of [
      "",
      "* * * * *",
      "*/30 * * * *",
      "0 * * * *",
      "0 9 * * *",
      "0 9 * * 1-5",
      "30 8 1 * *",
      "0 */2 * * *",
      "30 */6 * * *",
    ]) {
      expect(roundTrips(value)).toBe(true);
    }
  });

  it("rejects schedules that would lose information", () => {
    for (const value of [
      "0,30 9 * * *", // minute list
      "0 9-17 * * *", // hour range
      "0 0 1 * 1", // day-of-month AND day-of-week
      "0 0 * 6 *", // specific month
      "0 0 * * MON", // named day
      "garbage",
    ]) {
      expect(roundTrips(value)).toBe(false);
    }
  });
});

describe("parse/build round-trip on canonical form", () => {
  it("is a fixed point for representable values", () => {
    for (const value of [
      "* * * * *",
      "*/15 * * * *",
      "0 * * * *",
      "0 */2 * * *",
      "30 */12 * * *",
      "0 9 * * 1,3,5",
      "30 8 15 * *",
    ]) {
      const { freq, parts } = parseCron(value);
      expect(buildCron(freq, parts)).toBe(value);
    }
  });

  it("parses an hour step into the hour interval", () => {
    const { freq, parts } = parseCron("15 */3 * * *");
    expect(freq).toBe("hour");
    expect(parts.hourInterval).toBe(3);
    expect(parts.minute).toBe(15);
  });
});

describe("CronWidget rendering", () => {
  it("shows the visual builder for representable values", () => {
    render(<CronWidget {...widgetProps("0 9 * * 1-5")} />);
    expect(screen.getByText("Every")).toBeTruthy();
  });

  it("falls back to a raw input for lossy values, preserving the string", () => {
    render(<CronWidget {...widgetProps("0,30 9 * * *")} />);
    expect(screen.getByText(/too advanced/i)).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(
      "0,30 9 * * *",
    );
  });
});
