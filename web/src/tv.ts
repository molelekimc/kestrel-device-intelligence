// TV mode rotation: which scene is next, and how long each one stays up.
export const SCENES = ["floor", "cost", "why", "fixes"] as const;
export type Scene = (typeof SCENES)[number];
export const SCENE_SECONDS = 20;

/** Step through the scenes in either direction, wrapping around. */
export function step(i: number, dir: 1 | -1 = 1, n = SCENES.length): number {
  return (((i + dir) % n) + n) % n;
}

/** Hours lost to IT faults per week, summed over plants (suppressed rows left out). */
export function hoursByWeek(rows: Record<string, any>[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    if (r.suppressed || typeof r.it_minutes !== "number") continue;
    out[r.iso_week] = (out[r.iso_week] ?? 0) + r.it_minutes / 60;
  }
  return out;
}
