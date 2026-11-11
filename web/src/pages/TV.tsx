// TV mode: a wall screen for the plant. Large type, no clicking needed, rotates every 20 seconds
// (arrow keys step, space pauses), always on the latest week, and updates live after each refresh.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Columns } from "../components/charts";
import { BandBadge, BrandMark } from "../components/controlroom";
import { PlantFloor } from "../components/PlantFloor";
import { Badge } from "../components/ui";
import { niceDate, niceDateTime, num, shortWeek, zar } from "../format";
import { freshnessChips } from "../freshness";
import { duration, headline, likelyCause, type Band } from "../headline";
import { useLive, useTool } from "../live";
import { SCENE_SECONDS, SCENES, step, hoursByWeek } from "../tv";
import { weekWindow } from "./Overview";

const TITLES = { floor: "The plant floor", cost: "What it cost", why: "Why it happened", fixes: "Is the fix working?" };

export default function TV() {
  const { status, connected, week: chosen } = useLive();
  const weeks = status?.weeks ?? [];
  // TV follows the latest week unless a link pins one (?week=2026-W41).
  const linked = new URLSearchParams(window.location.search).has("week");
  const week = (linked ? chosen : weeks.at(-1)) ?? null;
  const { prev } = weekWindow(weeks, week);
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const go = useCallback((d: 1 | -1) => setI((x) => step(x, d)), []);

  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => go(1), SCENE_SECONDS * 1000);
    return () => clearInterval(t);
  }, [paused, go, i]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === " ") { e.preventDefault(); setPaused((p) => !p); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [go]);

  const floor = useTool("floor", week ? { week } : null);
  const prevFloor = useTool("floor", prev ? { week: prev } : null);
  const impact = useTool("business-impact", week ? { week_from: week, week_to: week } : null);
  const trend = useTool("business-impact", weeks.length ? { week_from: weeks[0], week_to: week } : null);
  const changes = useTool("changes", week ? { week_from: prev ?? week, week_to: week } : null);
  const rem = useTool("remediation", {});
  const fresh = useTool("freshness", {});

  const chips = freshnessChips(fresh.data?.rows ?? []);
  const dataTo = status?.data_to ?? null;
  const f = floor.data;
  const partialTo = f?.period?.end_date && dataTo && dataTo < f.period.end_date ? niceDate(dataTo).replace(/ \d{4}$/, "") : null;
  const top = likelyCause(changes.data?.rows);
  const h = f ? headline({ floor: f.rows, previousFloor: prevFloor.data?.rows, topChange: top,
    staleFeeds: chips.map((c) => c.label), partialTo }) : null;
  const hours = hoursByWeek(trend.data?.rows ?? []);
  const cost = impact.data?.cost_total_zar;
  const scene = SCENES[i];

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen?.();
  };

  return (
    <div className="cr tv">
      <header className="tv-top">
        <div className="cr-brand"><BrandMark size={40} /><strong>Kestrel Control Room</strong></div>
        <div className="tv-scene-title">{TITLES[scene]} · {week ? shortWeek(week) : ""}{partialTo ? " (so far)" : ""}</div>
        <div className="tv-controls">
          <span className={`live-dot ${connected ? "on" : ""}`}>{connected ? "Live" : "Offline"}</span>
          <button className="ghost" onClick={() => setPaused((p) => !p)}>{paused ? "Play" : "Pause"}</button>
          <button className="ghost" onClick={toggleFullscreen}>Full screen</button>
          <Link to="/" className="tv-exit">Exit</Link>
        </div>
      </header>
      <div className="tv-progress" aria-hidden="true">
        {SCENES.map((s, j) => (
          <i key={s} className={j === i ? `on ${paused ? "paused" : ""}` : j < i ? "done" : ""}
            style={j === i ? { animationDuration: `${SCENE_SECONDS}s` } : undefined} />
        ))}
      </div>

      <main className="tv-stage" aria-live="polite">
        {scene === "floor" && (
          <section className="tv-scene tv-floor">
            {h && <p className="tv-lead">{h.lead}</p>}
            {f && <PlantFloor rows={f.rows} compact />}
          </section>
        )}
        {scene === "cost" && (
          <section className="tv-scene tv-cost">
            <div className="tv-label">Estimated cost of IT disruption, {week}{partialTo ? " so far" : ""}</div>
            <div className="tv-hero">{cost ? `${zar(cost.low)} – ${zar(cost.high)}` : "…"}</div>
            <div className="tv-sub">Illustrative range from placeholder assumptions, not finance figures.</div>
            <div className="tv-chart">
              <div className="tv-label">Hours of production lost to IT faults, per week</div>
              <Columns labels={weeks} highlight={week} unit=" h"
                series={[{ name: "Hours lost to IT faults", values: weeks.map((w) => hours[w] !== undefined ? Math.round(hours[w] * 10) / 10 : null) }]} />
            </div>
          </section>
        )}
        {scene === "why" && (
          <section className="tv-scene tv-why">
            {top && h?.cause ? (
              <>
                <div className="tv-label">The change most likely behind it</div>
                <div className="tv-big">{top.description}</div>
                <div className="tv-meta"><Badge kind={top.evidence} /> {top.change_id} · {niceDateTime(top.actual_start)}</div>
                <p className="tv-text">The shift shows on the PCs this change was sent to, not on the others. Evidence, not proof.</p>
              </>
            ) : (
              <>
                <div className="tv-label">This week</div>
                <div className="tv-big">No production area is in trouble from IT faults.</div>
                <p className="tv-text">Every area is running normally, so there is nothing to explain. Past problems and their fixes are on the next screen.</p>
              </>
            )}
            {h?.worst && <p className="tv-text">{h.worst}</p>}
          </section>
        )}
        {scene === "fixes" && (
          <section className="tv-scene tv-fixes">
            <div className="tv-label">What's being done, and did it work</div>
            <ul className="tv-list">
              {(rem.data?.rows ?? []).map((r) => (
                <li key={r.action_id}><Badge kind={r.verdict} /> <span>{r.title}</span></li>
              ))}
            </ul>
            {f && (
              <div className="tv-areas">
                {f.rows.filter((r) => r.band).map((r) => (
                  <span key={`${r.plant}${r.area_code}`}><BandBadge band={r.band as Band} /> {r.area} <span className="meta">{r.plant_name}</span></span>
                ))}
              </div>
            )}
          </section>
        )}
      </main>

      <footer className="tv-ticker">
        {chips.length ? chips.map((c) => (
          <span key={c.label}><Badge kind={c.level} text={c.label} /> {c.reason}</span>
        )) : <span><Badge kind="fresh" text="All feeds fresh" /> Data as of {niceDateTime(status?.as_of)}</span>}
        {f && <span className="meta">{num(f.devices_total.in_use)} Windows PCs · {duration(f.rows.reduce((s: number, r: any) => s + (typeof r.it_minutes === "number" ? r.it_minutes : 0), 0))} lost to IT faults</span>}
      </footer>
    </div>
  );
}
