import { describe, expect, it } from "vitest";
import { ago, num, pct, signedPct, weekRange, zar } from "./format";
import { freshnessChips, overall } from "./freshness";

describe("format", () => {
  it("formats rand compactly", () => {
    expect(zar(2269468)).toBe("R2.3M");
    expect(zar(83300)).toBe("R83.3K");
    expect(zar(null)).toBe("–");
  });
  it("passes suppressed values through", () => {
    expect(num("<10")).toBe("<10");
    expect(pct("<10")).toBe("<10");
    expect(zar("<10")).toBe("<10");
  });
  it("formats shares and changes", () => {
    expect(pct(0.326)).toBe("33%");
    expect(signedPct(-0.84)).toBe("−84%");
    expect(signedPct(1.79)).toBe("+179%");
  });
  it("shortens week ranges", () => {
    expect(weekRange("2026-W39..2026-W39")).toBe("W39");
    expect(weekRange("2026-W40..2026-W41")).toBe("W40–W41");
  });
  it("says how long ago", () => {
    const now = new Date("2026-11-10T12:00:00Z");
    expect(ago("2026-11-10T11:58:00Z", now)).toBe("2 min ago");
    expect(ago(null, now)).toBe("never");
  });
});

describe("freshness chips", () => {
  const rows = [
    { status: "fresh", label: "Intune inventory", reason: "" },
    { status: "late", label: "MES line stops", reason: "export late" },
    { status: "stale", label: "Reliability telemetry - DBN", reason: "Mon 05 Oct: 0%" },
  ];
  it("lists only feeds behind SLA, worst first", () => {
    expect(freshnessChips(rows).map((c) => c.label)).toEqual(["Reliability telemetry - DBN", "MES line stops"]);
  });
  it("takes the worst status overall", () => {
    expect(overall(rows)).toBe("stale");
    expect(overall([rows[0]])).toBe("fresh");
  });
});
