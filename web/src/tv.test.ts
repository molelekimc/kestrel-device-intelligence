import { describe, expect, it } from "vitest";
import { hoursByWeek, SCENES, step } from "./tv";

describe("TV rotation", () => {
  it("goes floor → cost → why → fixes and wraps both ways", () => {
    expect(SCENES).toEqual(["floor", "cost", "why", "fixes"]);
    expect([0, 1, 2, 3].map((i) => step(i))).toEqual([1, 2, 3, 0]);
    expect(step(0, -1)).toBe(3);
  });

  it("sums hours lost per week and skips hidden rows", () => {
    expect(hoursByWeek([
      { iso_week: "W1", it_minutes: 90 }, { iso_week: "W1", it_minutes: 30 },
      { iso_week: "W2", suppressed: true, it_minutes: "<10" },
    ])).toEqual({ W1: 2 });
  });
});
