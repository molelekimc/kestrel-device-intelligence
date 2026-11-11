import { useState } from "react";
import { requestRefresh } from "../api";
import { ago, niceDateTime } from "../format";
import { freshnessChips, LEVEL_TEXT, overall } from "../freshness";
import { useLive, useTool } from "../live";
import { Badge } from "./ui";

/** Shown on every page: how current the data is, which feeds are behind, and a refresh control. */
export function FreshnessBar() {
  const { status, connected } = useLive();
  const { data } = useTool("freshness", {});
  const [asked, setAsked] = useState(false);
  const rows = data?.rows ?? [];
  const chips = freshnessChips(rows);
  const level = overall(rows);
  const last = status?.last_refresh;

  return (
    <div className={`freshness ${level}`} role="status" aria-live="polite">
      <div className="fresh-main">
        <Badge kind={level} text={LEVEL_TEXT[level]} />
        <span>
          Data as of <strong>{niceDateTime(status?.as_of)}</strong>
          {status?.replay && <span className="meta"> (simulated clock)</span>}
          {last && <> · last refresh {ago(last.finished_at)} ({last.status})</>}
        </span>
        <span className={`live-dot ${connected ? "on" : ""}`} title={connected ? "Live updates connected" : "Live updates offline"}>
          {connected ? "Live" : "Offline"}
        </span>
        {!status?.public && <button
          className="ghost"
          disabled={asked}
          onClick={() => requestRefresh().then(() => setAsked(true)).then(() => setTimeout(() => setAsked(false), 8000))}
        >
          {asked ? "Refresh requested" : "Refresh now"}
        </button>}
      </div>
      {chips.length > 0 && (
        <ul className="chips">
          {chips.map((c) => (
            <li key={c.label}>
              <Badge kind={c.level} text={c.label} /> <span className="meta">{c.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
