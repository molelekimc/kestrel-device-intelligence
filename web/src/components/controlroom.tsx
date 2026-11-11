// Small control-room pieces: brand mark, gauge, status ring, week scrubber and jargon terms.
import type { ReactNode } from "react";
import { shortWeek } from "../format";
import { BAND, type Band } from "../headline";

/** Original Kestrel mark: a hovering kestrel, wings spread, drawn as a few facets. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg className="brandmark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M2 12.5 L15 8.5 L13 15 Z" fill="var(--brand)" />
      <path d="M30 12.5 L17 8.5 L19 15 Z" fill="var(--brand)" opacity=".75" />
      <path d="M13 15 L16 7 L19 15 L17.4 24 L16 29 L14.6 24 Z" fill="var(--brand)" />
      <circle cx="16" cy="6.2" r="2.2" fill="var(--brand)" />
    </svg>
  );
}

/** Semicircular done-vs-planned gauge. Colour is the series green, not a status. */
export function Gauge({ value, max, title, doneLabel, totalLabel, note }:
  { value: number | null; max: number | null; title: string; doneLabel: string; totalLabel: string; note?: ReactNode }) {
  const share = value !== null && max ? Math.min(1, value / max) : 0;
  const arc = "M 12 72 A 60 60 0 0 1 132 72";
  return (
    <figure className="gauge">
      <figcaption>{title}</figcaption>
      <svg viewBox="0 0 144 82" role="img" aria-label={`${title}: ${value ?? "–"} of ${max ?? "–"}`}>
        <path d={arc} pathLength={100} className="gauge-track" />
        {share > 0 && <path d={arc} pathLength={100} className="gauge-value" strokeDasharray={`${share * 100} 100`} />}
        <text x="72" y="64" textAnchor="middle" className="gauge-figure">{value ?? "–"}</text>
        <text x="72" y="78" textAnchor="middle" className="gauge-of">of {max ?? "–"}</text>
      </svg>
      <div className="gauge-legend"><span><i className="done" />{doneLabel}</span><span><i className="total" />{totalLabel}</span></div>
      {note && <div className="gauge-note">{note}</div>}
    </figure>
  );
}

/** A status ring: how much of stopped time IT caused, with the band's icon (never colour alone). */
export function Ring({ share, band, size = 40 }: { share: number | null; band: Band | null; size?: number }) {
  const r = 15, c = 2 * Math.PI * r;
  const role = band ? BAND[band].role : "neutral";
  return (
    <svg className={`ring ${role}`} width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r={r} className="ring-track" />
      {share !== null && (
        <circle cx="20" cy="20" r={r} className="ring-value" strokeDasharray={`${Math.min(1, share) * c} ${c}`}
          transform="rotate(-90 20 20)" />
      )}
      <text x="20" y="24" textAnchor="middle">{share === null ? "–" : `${Math.round(share * 100)}`}</text>
    </svg>
  );
}

export function BandBadge({ band }: { band: Band | null }) {
  if (!band) return <span className="badge neutral"><span aria-hidden="true">·</span> Too few stops to show</span>;
  const b = BAND[band];
  return <span className={`badge ${b.role}`}><span aria-hidden="true">{b.icon}</span> {b.short}</span>;
}

const EVENT: Record<string, { mark: string; role: string; label: string }> = {
  change: { mark: "◆", role: "neutral", label: "Change" },
  major_incident: { mark: "●", role: "critical", label: "Major incident" },
  telemetry_outage: { mark: "▲", role: "warning", label: "Data outage" },
  remediation: { mark: "✓", role: "good", label: "Fix actioned" },
};

/** Week strip: pick a week; marks show what happened in each. */
export function WeekScrubber({ weeks, week, onPick, events }:
  { weeks: string[]; week: string | null; onPick: (w: string) => void; events: Record<string, Record<string, number>> }) {
  return (
    <div className="scrubber">
      <div className="scrubber-weeks" role="group" aria-label="Choose a week">
        {weeks.map((w) => {
          const ev = events[w] ?? {};
          const summary = Object.entries(ev).map(([k, n]) => `${n} ${EVENT[k]?.label.toLowerCase() ?? k}`).join(", ");
          return (
            <button key={w} className={`wk ${w === week ? "on" : ""}`} aria-pressed={w === week} onClick={() => onPick(w)}
              title={summary ? `${w}: ${summary}` : w}>
              <span className="wk-marks" aria-hidden="true">
                {Object.keys(EVENT).filter((k) => ev[k]).map((k) => <i key={k} className={EVENT[k].role}>{EVENT[k].mark}</i>)}
              </span>
              <span className="wk-label">{shortWeek(w)}</span>
              {summary && <span className="visually-hidden">: {summary}</span>}
            </button>
          );
        })}
      </div>
      <div className="scrubber-legend" aria-hidden="true">
        {Object.values(EVENT).map((e) => <span key={e.label}><i className={e.role}>{e.mark}</i> {e.label}</span>)}
      </div>
    </div>
  );
}

const TERMS: Record<string, string> = {
  MES: "Manufacturing Execution System: the shop-floor software that runs each production line. If its client on a station PC crashes, the line can stop.",
  telemetry: "Health data that each Windows PC sends automatically: start-ups, crashes, blue screens.",
  "blue screen": "A Windows crash that restarts the whole PC.",
  "line stop": "Any time a production line stands still, logged by the MES with a reason.",
};

/** A jargon word with a plain-English explanation on hover or focus. */
export function Term({ children }: { children: keyof typeof TERMS | string }) {
  const text = TERMS[children as string];
  return text ? <abbr className="term" title={text} tabIndex={0}>{children}</abbr> : <>{children}</>;
}
