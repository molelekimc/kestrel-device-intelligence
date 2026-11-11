// The control room: the business view of the fleet for a plant/ops manager. Plain language first,
// the plant floor in the middle, and every number one click away from its detail page.
import { Link, NavLink } from "react-router-dom";
import { BrandMark, Gauge, Term, WeekScrubber } from "../components/controlroom";
import { PlantFloor } from "../components/PlantFloor";
import { Badge, Loading } from "../components/ui";
import { niceDate, niceDateTime, num, pct, zar } from "../format";
import { freshnessChips, LEVEL_TEXT, overall } from "../freshness";
import { headline } from "../headline";
import { useLive, useTool } from "../live";
import { weekWindow } from "./Overview";

function TopBar() {
  const { status, connected } = useLive();
  const fresh = useTool("freshness", {});
  const level = overall(fresh.data?.rows ?? []);
  return (
    <header className="cr-top">
      <div className="cr-brand">
        <BrandMark />
        <div>
          <strong>Kestrel Control Room</strong>
          <span className="meta">Kestrel Assembly Group · fictional client, synthetic data</span>
        </div>
      </div>
      <nav className="cr-nav" aria-label="Pages">
        <NavLink to="/" end>Control room</NavLink>
        <NavLink to="/detail">Detail</NavLink>
        <NavLink to="/why">Why</NavLink>
        <NavLink to="/ask">Ask the fleet</NavLink>
      </nav>
      <div className="cr-live">
        <Badge kind={level} text={LEVEL_TEXT[level]} />
        <span className={`live-dot ${connected ? "on" : ""}`}>{connected ? "Live" : "Offline"}</span>
        <span className="meta">data as of {niceDateTime(status?.as_of)}{status?.replay ? " (simulated)" : ""}</span>
      </div>
    </header>
  );
}

export default function ControlRoom() {
  const { status, week, setWeek } = useLive();
  const weeks = status?.weeks ?? [];
  const { prev } = weekWindow(weeks, week);
  const floor = useTool("floor", week ? { week } : null);
  const prevFloor = useTool("floor", prev ? { week: prev } : null);
  const impact = useTool("business-impact", week ? { week_from: week, week_to: week } : null);
  const changes = useTool("changes", week ? { week_from: prev ?? week, week_to: week } : null);
  const timeline = useTool("timeline", weeks.length ? { week_from: weeks[0], week_to: weeks.at(-1) } : null);
  const fresh = useTool("freshness", {});

  const events: Record<string, Record<string, number>> = {};
  for (const e of timeline.data?.rows ?? []) {
    const w = (events[e.iso_week] ??= {});
    w[e.kind] = (w[e.kind] ?? 0) + 1;
  }
  const stale = freshnessChips(fresh.data?.rows ?? []).map((c) => c.label);
  const f = floor.data;
  // Data stops before the end of the week: say "so far" rather than compare with a full week.
  const dataTo = status?.data_to ?? null;
  const partialTo = f?.period?.end_date && dataTo && dataTo < f.period.end_date ? niceDate(dataTo).replace(/ \d{4}$/, "") : null;
  const h = f ? headline({ floor: f.rows, previousFloor: prevFloor.data?.rows, topChange: changes.data?.rows?.find((c) => !c.suppressed),
    staleFeeds: stale, partialTo }) : null;
  const cost = impact.data?.cost_total_zar;
  const dev = f?.devices_total;
  const silentPlants = (f?.devices_by_plant ?? []).filter((d: any) => typeof d.silent === "number" && d.silent / d.in_use > 0.3);
  const plantName: Record<string, string> = Object.fromEntries((f?.rows ?? []).map((r) => [r.plant, r.plant_name]));

  return (
    <div className="cr">
      <div className="cr-page">
        <TopBar />
        <p className="cr-mission">
          When factory PCs and the <Term>MES</Term> client fail, production lines stop. This shows <strong>where</strong> it is
          happening, <strong>what it costs</strong>, <strong>why</strong>, and <strong>whether the fix is working</strong>.
        </p>
        {weeks.length > 0 && <WeekScrubber weeks={weeks} week={week} onPick={setWeek} events={events} />}

        <div className="cr-grid">
          <aside className="cr-side">
            <section className="panel story">
              <h2>{week} in one line</h2>
              {h ? (
                <>
                  <p className="lead">{h.lead}</p>
                  {h.worst && <p>{h.worst}</p>}
                  {h.cause && <p className="cause">{h.cause} <Link to="/why">Evidence →</Link></p>}
                  {h.caveat && <p className="caveat"><Badge kind="warning" text="Data gap" /> {h.caveat}</p>}
                </>
              ) : <Loading error={floor.error} />}
            </section>
            <section className="panel">
              <h2>Cost of IT disruption</h2>
              <div className="big">{cost ? `${zar(cost.low)} – ${zar(cost.high)}` : "…"}</div>
              <p className="meta">Illustrative range: lost production, people waiting and service desk time, from placeholder
                assumptions, not finance figures. <Link to="/detail">Breakdown →</Link></p>
            </section>
            <section className="panel gauges">
              <Gauge title="Fixes" value={f?.fixes.done ?? null} max={f?.fixes.planned ?? null}
                doneLabel="Done" totalLabel="In the plan"
                note={f ? <><strong>{f.fixes.proven_effective}</strong> proven to work · <Link to="/remediation">Details →</Link></> : null} />
              <Gauge title="Incidents" value={typeof f?.incidents.resolved === "number" ? f.incidents.resolved : null}
                max={typeof f?.incidents.opened === "number" ? f.incidents.opened : null}
                doneLabel="Resolved" totalLabel="Opened this week"
                note={<Link to="/service-desk">Service desk →</Link>} />
            </section>
          </aside>

          <section className="panel floor-panel" aria-labelledby="floor-title">
            <div className="floor-head">
              <h2 id="floor-title">The plant floor, {week}{partialTo ? ` (so far, to ${partialTo})` : ""}</h2>
              <span className="meta">Ring = share of <Term>line stop</Term> time caused by IT faults</span>
            </div>
            {f ? <PlantFloor rows={f.rows} /> : <Loading error={floor.error} />}
            {dev && (
              <div className="devices">
                <div className="dev-total"><span className="meta">Windows PCs in use</span><strong>{num(dev.in_use)}</strong></div>
                <div className="dev good"><span aria-hidden="true">✓</span><span className="meta">Healthy</span><strong>{num(dev.healthy)}</strong></div>
                <div className="dev warning"><span aria-hidden="true">!</span><span className="meta">Crashed at least once</span><strong>{num(dev.having_problems)}</strong></div>
                <div className="dev neutral"><span aria-hidden="true">?</span><span className="meta">Silent (no <Term>telemetry</Term>)</span><strong>{num(dev.silent)}</strong></div>
                {silentPlants.length > 0 && (
                  <p className="dev-note">
                    <Badge kind="warning" text="Not healthy, just quiet" />{" "}
                    {silentPlants.map((d: any) => `${plantName[d.plant] ?? d.plant}: ${pct(d.silent / d.in_use)} of PCs sent no data`).join("; ")}.
                    {" "}<Link to="/data">Data trust →</Link>
                  </p>
                )}
              </div>
            )}
          </section>
        </div>

        <ol className="how">
          <li><strong>PCs report their health</strong><span>Start-ups, crashes and <Term>blue screen</Term>s from {dev ? `${num(dev.in_use)} Windows PCs` : "every Windows PC"}.</span></li>
          <li><strong>Linked to the factory</strong><span>Matched to line stops, service desk tickets and logged changes.</span></li>
          <li><strong>Cost, cause and fix</strong><span>What it cost, the likely cause, and whether each fix worked.</span></li>
          <li className="how-cta"><Link to="/ask">Ask a question in plain English →</Link></li>
        </ol>
      </div>
    </div>
  );
}
