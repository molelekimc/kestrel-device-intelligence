// Isometric geometry for the plant floor: world units (x along a hall, y across, z up) to SVG points.
import type { Row } from "./api";

const COS = Math.cos(Math.PI / 6);
const SIN = 0.5;
export const UNIT = 24;                       // px per world unit before the viewBox scales it

export type Pt = [number, number];

export function project(x: number, y: number, z = 0): Pt {
  return [(x - y) * COS * UNIT, (x + y) * SIN * UNIT - z * UNIT];
}

export const pts = (ps: Pt[]) => ps.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");

/** The three visible faces (top, left, right) of a box at (x, y, z) with size w x d x h. */
export function box(x: number, y: number, z: number, w: number, d: number, h: number) {
  const p = (dx: number, dy: number, dz: number) => project(x + dx, y + dy, z + dz);
  return {
    top: [p(0, 0, h), p(w, 0, h), p(w, d, h), p(0, d, h)],
    left: [p(0, d, 0), p(w, d, 0), p(w, d, h), p(0, d, h)],
    right: [p(w, 0, 0), p(w, d, 0), p(w, d, h), p(w, 0, h)],
  };
}

export interface AreaLayout {
  key: string;            // "ROS|BIW"
  row: Row;
  x: number; y: number; w: number; d: number;
  label: Pt;              // where the status tag sits (above the platform's centre)
}

export interface HallLayout {
  plant: string;
  name: string;
  x: number; y: number; w: number; d: number;
  label: Pt;
  areas: AreaLayout[];
}

export const LINE_GAP = 1.1;     // world units between conveyor lines
export const AREA_DEPTH = 4;     // how deep an area platform is
const AREA_PAD = 0.8;
const HALL_GAP = 4.5;

/** Halls one behind another (y), each with its areas side by side (x), one conveyor per line. */
export function layoutFloor(rows: Row[], plantOrder = ["ROS", "GQB", "DBN"]): { halls: HallLayout[]; bounds: [number, number, number, number] } {
  const halls: HallLayout[] = [];
  let y = 0;
  for (const plant of plantOrder) {
    const areas = rows.filter((r) => r.plant === plant);
    if (!areas.length) continue;
    let x = AREA_PAD;
    const laid: AreaLayout[] = areas.map((r) => {
      const w = Math.max(2, r.lines) * LINE_GAP + 0.4;
      const a: AreaLayout = { key: `${plant}|${r.area_code}`, row: r, x, y: y + AREA_PAD, w, d: AREA_DEPTH,
        label: project(x + w / 2, y + AREA_PAD + AREA_DEPTH / 2, 2.2) };
      x += w + AREA_PAD;
      return a;
    });
    const d = AREA_DEPTH + 2 * AREA_PAD;
    halls.push({ plant, name: areas[0].plant_name, x: 0, y, w: x, d, label: project(x, y + 0.6, 0), areas: laid });
    y += d + HALL_GAP;
  }
  // Bounds of every hall corner plus the raised labels.
  const corners: Pt[] = halls.flatMap((h) => [project(h.x, h.y), project(h.x + h.w, h.y), project(h.x, h.y + h.d),
    project(h.x + h.w, h.y + h.d), project(h.x + h.w + 5, h.y), ...h.areas.map((a) => a.label)]);
  const xs = corners.map((c) => c[0]), ys = corners.map((c) => c[1]);
  const pad = UNIT * 1.5;
  return { halls, bounds: [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad] };
}
