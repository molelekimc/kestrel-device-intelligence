import { describe, expect, it } from "vitest";
import { areaSentence, duration, headline, likelyCause, plantMinutes } from "./headline";
import { layoutFloor, project, UNIT } from "./iso";

const area = (plant: string, plant_name: string, area_code: string, areaName: string, it: number, all: number, band: string, lines = 6) => ({
  plant, plant_name, area_code, area: areaName, lines, stops: 40, it_minutes: it, downtime_minutes: all,
  it_app_minutes: it * 0.8, it_downtime_share: it / all, band,
});
const W41 = [
  area("ROS", "Rosslyn", "BIW", "Body shop", 500, 1500, "hurting"),
  area("GQB", "Gqeberha", "AXLE", "Axles", 600, 1500, "hurting"),
  area("DBN", "Durban", "SEAT", "Seats", 30, 600, "normal", 5),
  { plant: "DBN", plant_name: "Durban", area_code: "HARN", area: "Wiring harnesses", lines: 5, suppressed: true, stops: "<10", band: null },
];
const W40 = [area("ROS", "Rosslyn", "BIW", "Body shop", 120, 1500, "normal"), area("GQB", "Gqeberha", "AXLE", "Axles", 100, 1500, "normal")];

describe("durations", () => {
  it("reads minutes and hours naturally", () => {
    expect(duration(45)).toBe("45 minutes");
    expect(duration(60)).toBe("1 hour");
    expect(duration(803.5)).toBe("13.4 hours");
    expect(duration(4367)).toBe("73 hours");
    expect(duration(null)).toBe("–");
  });
});

describe("control room headline", () => {
  it("leads with lost production time and the comparison", () => {
    const h = headline({ floor: W41, previousFloor: W40 });
    expect(h.lead).toBe("IT faults stopped the production lines for 18.8 hours this week, 5.1 times the week before (3.7 hours).");
    expect(h.worst).toBe("Hardest hit: Axles at Gqeberha, which lost 10 hours. 2 of 4 areas are hurting.");
  });

  it("leaves suppressed areas out of the totals", () => {
    expect(plantMinutes(W41).map((p) => p.plant)).toEqual(["GQB", "ROS", "DBN"]);
  });

  it("names a cause only with moderate or strong evidence, and labels it", () => {
    const strong = headline({ floor: W41, topChange: { change_id: "CHG1", description: "Deploy app 4.2", evidence: "strong", top_metric: "app_crash_rate", direction: "increase" } });
    expect(strong.cause).toContain("not proof");
    expect(headline({ floor: W41, topChange: { change_id: "CHG1", description: "x", evidence: "weak" } }).cause).toBeUndefined();
  });

  it("never compares a week in progress with a full week", () => {
    const h = headline({ floor: W41, previousFloor: W40, partialTo: "Thu 15 Oct" });
    expect(h.lead).toBe("So far this week (data to Thu 15 Oct), IT faults have stopped the production lines for 18.8 hours.");
  });

  it("offers no cause when every area is running normally", () => {
    const calm = W41.map((r) => ({ ...r, band: r.band && "normal" }));
    expect(headline({ floor: calm, topChange: { change_id: "C", description: "x", evidence: "strong" } }).cause).toBeUndefined();
    expect(headline({ floor: calm }).worst).toBe("Every area is running normally. Most IT stop time: Axles at Gqeberha (10 hours).");
  });

  it("only blames a change that made things worse", () => {
    const fix = { change_id: "HOT", description: "hotfix", evidence: "strong", top_metric: "app_crash_rate", direction: "decrease" };
    const bad = { change_id: "BAD", description: "release", evidence: "moderate", top_metric: "app_crash_rate", direction: "increase" };
    const outage = { change_id: "NET", description: "proxy", evidence: "strong", top_metric: "coverage", direction: "decrease" };
    expect(likelyCause([fix, bad])?.change_id).toBe("BAD");
    expect(likelyCause([outage])?.change_id).toBe("NET");
    expect(headline({ floor: W41, topChange: fix }).cause).toBeUndefined();
  });

  it("warns when feeds are behind", () => {
    expect(headline({ floor: W41, staleFeeds: ["Reliability telemetry - DBN"] }).caveat).toContain("MES log");
  });

  it("explains an area in one sentence, or why it is hidden", () => {
    expect(areaSentence(W41[1])).toBe(
      "Axles at Gqeberha: hurting. IT faults caused 40% of stopped time (10 hours of 25 hours, 40 stops on 6 lines). 8 hours of it was the MES client.");
    expect(areaSentence(W41[3])).toContain("hidden");
  });
});

describe("isometric floor", () => {
  it("projects the axes at 30 degrees", () => {
    expect(project(0, 0)).toEqual([0, 0]);
    const [x, y] = project(1, 0);
    expect(x).toBeCloseTo(Math.cos(Math.PI / 6) * UNIT);
    expect(y).toBeCloseTo(UNIT / 2);
    expect(project(0, 0, 1)[1]).toBe(-UNIT);
  });

  it("lays out one hall per plant with areas side by side, never overlapping", () => {
    const { halls, bounds } = layoutFloor(W41);
    expect(halls.map((h) => h.plant)).toEqual(["ROS", "GQB", "DBN"]);
    const dbn = halls[2].areas;
    expect(dbn).toHaveLength(2);
    expect(dbn[1].x).toBeGreaterThanOrEqual(dbn[0].x + dbn[0].w);
    expect(halls[1].y).toBeGreaterThanOrEqual(halls[0].y + halls[0].d);
    expect(bounds[2]).toBeGreaterThan(bounds[0]);
  });
});
