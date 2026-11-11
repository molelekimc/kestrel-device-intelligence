// The plant floor: one isometric hall per plant, its production areas as platforms, one conveyor
// per line with laptops and station PCs at the stations. Each area carries a status tag (ring +
// band icon + label); hovering or focusing it explains the area in a sentence.
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Row } from "../api";
import { pct } from "../format";
import { areaSentence, BAND, duration, type Band } from "../headline";
import { box, layoutFloor, LINE_GAP, project, pts, type Pt } from "../iso";

const PLANTS = [["ALL", "All plants"], ["ROS", "Rosslyn"], ["GQB", "Gqeberha"], ["DBN", "Durban"]] as const;
import { DataView } from "./ui";
import { BandBadge, Ring } from "./controlroom";

const roleOf = (r: Row) => (r.band ? BAND[r.band as Band].role : "neutral");

function Faces({ b, cls }: { b: ReturnType<typeof box>; cls: string }) {
  return (
    <g className={cls}>
      <polygon className="f-left" points={pts(b.left)} />
      <polygon className="f-right" points={pts(b.right)} />
      <polygon className="f-top" points={pts(b.top)} />
    </g>
  );
}

const K = 1.8;   // device drawing scale, in world units per design unit

/** A station PC: tower beside a monitor on a small desk. */
function StationPC({ x, y, z, role }: { x: number; y: number; z: number; role: string }) {
  const p = (dx: number, dy: number, dz: number) => project(x + dx * K, y + dy * K, z + dz * K);
  const b = (dx: number, dy: number, dz: number, w: number, d: number, h: number) =>
    box(x + dx * K, y + dy * K, z + dz * K, w * K, d * K, h * K);
  const screen: Pt[] = [p(0.05, 0.05, 0.42), p(0.05, 0.45, 0.42), p(0.05, 0.45, 0.78), p(0.05, 0.05, 0.78)];
  return (
    <g>
      <Faces b={b(0, 0, 0, 0.42, 0.5, 0.36)} cls="desk" />
      <Faces b={b(0.08, 0.52, 0, 0.26, 0.2, 0.62)} cls="tower" />
      <polygon className={`screen ${role}`} points={pts(screen)} />
    </g>
  );
}

/** A laptop: open lid on a small stand. */
function Laptop({ x, y, z, role }: { x: number; y: number; z: number; role: string }) {
  const p = (dx: number, dy: number, dz: number) => project(x + dx * K, y + dy * K, z + dz * K);
  const b = (dx: number, dy: number, dz: number, w: number, d: number, h: number) =>
    box(x + dx * K, y + dy * K, z + dz * K, w * K, d * K, h * K);
  const lid: Pt[] = [p(0.06, 0.08, 0.4), p(0.06, 0.5, 0.4), p(-0.04, 0.5, 0.72), p(-0.04, 0.08, 0.72)];
  return (
    <g>
      <Faces b={b(0, 0, 0, 0.42, 0.58, 0.34)} cls="desk" />
      <Faces b={b(0.06, 0.08, 0.34, 0.3, 0.42, 0.04)} cls="laptop-base" />
      <polygon className={`screen ${role}`} points={pts(lid)} />
    </g>
  );
}

export function PlantFloor({ rows, footer }: { rows: Row[]; footer?: ReactNode }) {
  const [focus, setFocus] = useState<string | null>(null);
  const [plant, setPlant] = useState<string>("ALL");
  const { halls, bounds } = useMemo(() => layoutFloor(rows, plant === "ALL" ? undefined : [plant]), [rows, plant]);
  const [x0, y0, x1, y1] = bounds;
  const W = x1 - x0, H = y1 - y0;
  const at = ([x, y]: Pt) => ({ left: `${((x - x0) / W) * 100}%`, top: `${((y - y0) / H) * 100}%` });
  const focused = halls.flatMap((h) => h.areas).find((a) => a.key === focus);

  // Painter's order: things further back (smaller x + y) first.
  const items: { depth: number; el: ReactNode }[] = [];
  for (const h of halls) {
    items.push({ depth: -1e6 + h.y, el: <Faces key={`hall-${h.plant}`} b={box(h.x, h.y, -0.3, h.w, h.d, 0.3)} cls="hall" /> });
    for (const a of h.areas) {
      const role = roleOf(a.row);
      items.push({ depth: -1e5 + a.x + a.y,
        el: <Faces key={`pl-${a.key}`} b={box(a.x, a.y, 0, a.w, a.d, 0.12)} cls={`platform ${role} ${a.key === focus ? "focus" : ""}`} /> });
      for (let i = 0; i < Math.max(2, a.row.lines); i++) {
        const lx = a.x + 0.35 + i * LINE_GAP;
        items.push({ depth: lx + a.y, el: <Faces key={`cv-${a.key}-${i}`} b={box(lx, a.y + 0.3, 0.12, 0.34, a.d - 0.6, 0.1)} cls="conveyor" /> });
        const sx = lx - 0.05, sy = a.y + a.d - 1.35;
        const Device = i % 2 ? Laptop : StationPC;
        items.push({ depth: sx + sy + 0.5, el: <Device key={`st-${a.key}-${i}`} x={sx} y={sy} z={0.22} role={role} /> });
      }
    }
  }
  items.sort((p, q) => p.depth - q.depth);

  return (
    <div className="floor">
      <div className="plant-switch" role="group" aria-label="Show plant">
        {PLANTS.map(([code, name]) => (
          <button key={code} className={plant === code ? "on" : ""} aria-pressed={plant === code}
            onClick={() => { setPlant(code); setFocus(null); }}>{name}</button>
        ))}
      </div>
      <div className="floor-stage" style={{ aspectRatio: `${W} / ${H}` }}>
        <svg viewBox={`${x0} ${y0} ${W} ${H}`} className="floor-svg" aria-hidden="true">{items.map((i) => i.el)}</svg>
        {halls.map((h) => (
          <span key={h.plant} className="hall-label" style={at(h.label)}>{h.name}</span>
        ))}
        {halls.flatMap((h) => h.areas).map((a) => {
          const r = a.row;
          return (
            <button key={a.key} className={`area-tag ${roleOf(r)} ${a.key === focus ? "on" : ""}`} style={at(a.label)}
              onMouseEnter={() => setFocus(a.key)} onFocus={() => setFocus(a.key)} onClick={() => setFocus(a.key)}
              aria-label={areaSentence(r)}>
              <i className="tag-bar" aria-hidden="true" />
              <span className="tag-name">{r.area}</span>
              <Ring share={r.suppressed ? null : r.it_downtime_share} band={r.band} size={34} />
              <span className="tag-icon" aria-hidden="true">{r.band ? BAND[r.band as Band].icon : "·"}</span>
            </button>
          );
        })}
      </div>

      <div className="floor-card" aria-live="polite">
        {focused ? (
          <>
            <BandBadge band={focused.row.band} /> <span>{areaSentence(focused.row)}</span>{" "}
            <Link to="/why">See what changed →</Link>
          </>
        ) : <span className="note">Hover or tab to an area to see what happened there. The ring shows how much of the time
          the line stood still was caused by IT faults.</span>}
      </div>

      {/* Phone: the same areas as cards. */}
      <ul className="floor-cards">
        {halls.flatMap((h) => h.areas).map(({ key, row: r }) => (
          <li key={key} className={roleOf(r)}>
            <Ring share={r.suppressed ? null : r.it_downtime_share} band={r.band} size={44} />
            <div>
              <strong>{r.area}</strong> <span className="meta">{r.plant_name}</span><br />
              <BandBadge band={r.band} />{" "}
              {!r.suppressed && <span className="meta">{duration(r.it_minutes)} lost to IT</span>}
            </div>
          </li>
        ))}
      </ul>

      <DataView headers={["Plant", "Area", "Lines", "Line stops", "IT stop time", "All stop time", "IT share", "Status"]}
        rows={rows.map((r) => [r.plant_name, r.area, r.lines, r.stops, r.suppressed ? "hidden" : duration(r.it_minutes),
          r.suppressed ? "hidden" : duration(r.downtime_minutes), r.suppressed ? "hidden" : pct(r.it_downtime_share),
          r.band ? BAND[r.band as Band].short : "too few stops"])} />
      {footer}
    </div>
  );
}
