import type { Row } from "./api";

export type Level = "fresh" | "late" | "stale" | "missing";
const RANK: Record<Level, number> = { fresh: 0, late: 1, stale: 2, missing: 3 };

export interface Chip {
  level: Level;
  label: string;
  reason: string;
}

/** Chips for the freshness bar: every feed behind its SLA, worst first. */
export function freshnessChips(rows: Row[]): Chip[] {
  return rows
    .filter((r) => r.status !== "fresh")
    .map((r) => ({ level: r.status as Level, label: r.label as string, reason: r.reason as string }))
    .sort((a, b) => RANK[b.level] - RANK[a.level] || a.label.localeCompare(b.label));
}

export function overall(rows: Row[]): Level {
  return rows.reduce<Level>((w, r) => (RANK[r.status as Level] > RANK[w] ? (r.status as Level) : w), "fresh");
}

export const LEVEL_TEXT: Record<Level, string> = {
  fresh: "All feeds fresh",
  late: "A feed is late",
  stale: "A feed is stale",
  missing: "A feed is missing",
};
