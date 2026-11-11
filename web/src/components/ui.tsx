import { useState, type ReactNode } from "react";
import type { Warning } from "../api";

// Status badges: colour never carries meaning alone (icon + label). Evidence strength is
// not a severity, so it uses the neutral style.
const BADGES: Record<string, [string, string, string]> = {
  effective: ["good", "✓", "Effective"],
  no_detectable_effect: ["warning", "–", "No detectable effect"],
  claimed_not_implemented: ["critical", "✕", "Claimed, not implemented"],
  not_yet_measurable: ["neutral", "…", "Not yet measurable"],
  not_measurable_process: ["neutral", "·", "Process change"],
  open: ["serious", "!", "Open"],
  inconclusive: ["warning", "?", "Inconclusive"],
  worse: ["critical", "✕", "Worse after action"],
  strong: ["neutral", "●", "Strong evidence"],
  moderate: ["neutral", "◐", "Moderate evidence"],
  weak: ["neutral", "○", "Weak evidence"],
  critical: ["critical", "✕", "Critical"],
  warning: ["warning", "!", "Warning"],
  info: ["neutral", "i", "Info"],
  fresh: ["good", "✓", "Fresh"],
  late: ["warning", "!", "Late"],
  stale: ["critical", "✕", "Stale"],
  missing: ["critical", "✕", "Missing"],
  ok: ["good", "✓", "OK"],
  failed: ["critical", "✕", "Failed"],
};

export function Badge({ kind, text }: { kind: string; text?: string }) {
  const [role, icon, label] = BADGES[kind] ?? ["neutral", "·", kind.replace(/_/g, " ")];
  return (
    <span className={`badge ${role}`}>
      <span aria-hidden="true">{icon}</span> {text ?? label}
    </span>
  );
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Table({ headers, rows, caption }: { headers: string[]; rows: ReactNode[][]; caption?: string }) {
  return (
    <div className="tablewrap">
      <table>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DataView({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Table view</summary>
      {open && <Table headers={headers} rows={rows.map((r) => r.map(String))} />}
    </details>
  );
}

export function Warnings({ items }: { items: Warning[] }) {
  const shown = items.filter((w) => w.severity !== "info");
  if (!shown.length) return <p className="note">No coverage or data quality warnings for this view.</p>;
  return (
    <ul className="warnings">
      {shown.map((w, i) => (
        <li key={i}>
          <Badge kind={w.severity} /> <span className="meta">{w.week} · {w.scope}</span> {w.message}
        </li>
      ))}
    </ul>
  );
}

export function Loading({ error }: { error?: string | null }) {
  return error ? <p className="error">Couldn't load this: {error}</p> : <p className="note">Loading…</p>;
}
