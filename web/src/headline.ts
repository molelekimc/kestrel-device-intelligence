// Plain-English sentences for the control room, built from governed tool results.
// Deterministic templates (no AI), so the same numbers always read the same way.
import type { Row } from "./api";

export type Band = "normal" | "watch" | "hurting";

export const BAND: Record<Band, { role: string; icon: string; short: string }> = {
  normal: { role: "good", icon: "✓", short: "Running normally" },
  watch: { role: "warning", icon: "!", short: "Watch" },
  hurting: { role: "critical", icon: "✕", short: "Hurting" },
};

/** 803.5 -> "13.4 hours", 45 -> "45 minutes", 60 -> "1 hour". */
export function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "–";
  if (minutes < 59.5) return `${Math.round(minutes)} minute${Math.round(minutes) === 1 ? "" : "s"}`;
  const h = minutes / 60;
  const text = h < 20 ? h.toFixed(1).replace(/\.0$/, "") : Math.round(h).toString();
  return `${text} hour${text === "1" ? "" : "s"}`;
}

const shown = (r: Row) => !r.suppressed && typeof r.it_minutes === "number";

/** IT minutes per plant from floor rows (suppressed areas left out). */
export function plantMinutes(rows: Row[]): { plant: string; name: string; minutes: number }[] {
  const by = new Map<string, { plant: string; name: string; minutes: number }>();
  for (const r of rows.filter(shown)) {
    const p = by.get(r.plant) ?? { plant: r.plant, name: r.plant_name, minutes: 0 };
    p.minutes += r.it_minutes;
    by.set(r.plant, p);
  }
  return [...by.values()].sort((a, b) => b.minutes - a.minutes);
}

/** Did this change make things worse? Coverage is bad when it falls; every other metric when it rises. */
export function worsened(c: Row | null | undefined): boolean {
  if (!c || !c.direction) return false;
  return c.top_metric === "coverage" ? c.direction === "decrease" : c.direction === "increase";
}

/** The strongest logged change that made things worse (rows arrive strongest first). */
export function likelyCause(changes: Row[] | undefined): Row | null {
  return (changes ?? []).find((c) => !c.suppressed && (c.evidence === "strong" || c.evidence === "moderate") && worsened(c)) ?? null;
}

export interface Headline {
  lead: string;          // the week in one sentence
  worst?: string;        // where it hurt most
  cause?: string;        // the most likely explanation, labelled as such
  caveat?: string;       // what to bear in mind about the data
}

export interface HeadlineInput {
  floor: Row[];                      // get_floor_status rows for the week
  previousFloor?: Row[] | null;      // the week before, for a comparison
  topChange?: Row | null;            // strongest logged change from get_change_correlation
  staleFeeds?: string[];             // labels of feeds behind their SLA
  partialTo?: string | null;         // the week is still running: data runs to this day (e.g. "Thu 15 Oct")
}

export function headline({ floor, previousFloor, topChange, staleFeeds = [], partialTo }: HeadlineInput): Headline {
  const total = plantMinutes(floor).reduce((s, p) => s + p.minutes, 0);
  const before = previousFloor ? plantMinutes(previousFloor).reduce((s, p) => s + p.minutes, 0) : null;
  // A week still in progress is never compared with a full one.
  let lead = partialTo
    ? `So far this week (data to ${partialTo}), IT faults have stopped the production lines for ${duration(total)}`
    : `IT faults stopped the production lines for ${duration(total)} this week`;
  if (!partialTo && before !== null && before > 0) {
    const ratio = total / before;
    lead += ratio >= 1.5 ? `, ${ratio.toFixed(1).replace(/\.0$/, "")} times the week before (${duration(before)})`
      : ratio <= 0.67 ? `, down from ${duration(before)} the week before`
      : `, about the same as the week before`;
  }
  lead += ".";

  const out: Headline = { lead };
  const worstArea = floor.filter(shown).sort((a, b) => b.it_minutes - a.it_minutes)[0];
  const hurting = floor.filter((r) => r.band === "hurting");
  // Only explain a problem when there is one: some area on watch or hurting.
  const troubled = floor.some((r) => r.band === "watch" || r.band === "hurting");
  if (worstArea && total > 0 && troubled) {
    out.worst = `Hardest hit: ${worstArea.area} at ${worstArea.plant_name}, which lost ${duration(worstArea.it_minutes)}`;
    out.worst += hurting.length > 1 ? `. ${hurting.length} of ${floor.length} areas are hurting.` : ".";
  } else if (worstArea && total > 0) {
    out.worst = `Every area is running normally. Most IT stop time: ${worstArea.area} at ${worstArea.plant_name} (${duration(worstArea.it_minutes)}).`;
  }
  if (troubled && topChange && (topChange.evidence === "strong" || topChange.evidence === "moderate") && worsened(topChange)) {
    out.cause = `Likely cause: “${topChange.description}” (${topChange.change_id}), ${topChange.evidence} evidence. A lead to check, not proof.`;
  }
  if (staleFeeds.length) {
    out.caveat = `Some data is behind (${staleFeeds.join(", ")}). Line stops come from the MES log and are still complete; device numbers for the affected plant are not.`;
  }
  return out;
}

/** The one-line card text for a production area. */
export function areaSentence(r: Row): string {
  if (r.suppressed) return `${r.area} at ${r.plant_name}: fewer than 10 line stops this week, so the figures are hidden.`;
  const b = BAND[(r.band ?? "normal") as Band];
  const share = Math.round((r.it_downtime_share ?? 0) * 100);
  const mes = r.it_app_minutes > 0 ? ` ${duration(r.it_app_minutes)} of it was the MES client.` : "";
  return `${r.area} at ${r.plant_name}: ${b.short.toLowerCase()}. IT faults caused ${share}% of stopped time ` +
    `(${duration(r.it_minutes)} of ${duration(r.downtime_minutes)}, ${r.stops} stops on ${r.lines} lines).${mes}`;
}
