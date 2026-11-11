import { Columns, Legend, Lines } from "../components/charts";
import { Card, DataView, Loading, Tile, Warnings } from "../components/ui";
import { num, pct, shortWeek, signedPct, zar } from "../format";
import { useLive, useTool } from "../live";

export function weekWindow(weeks: string[], week: string | null) {
  const i = week ? weeks.indexOf(week) : -1;
  return { first: weeks[0], prev: i > 0 ? weeks[i - 1] : null, upto: i >= 0 ? weeks.slice(0, i + 1) : weeks };
}

export default function Overview() {
  const { status, week } = useLive();
  const { first, prev, upto } = weekWindow(status?.weeks ?? [], week);
  const ready = week && first;
  const impact = useTool("business-impact", ready ? { week_from: week, week_to: week } : null);
  const impactPrev = useTool("business-impact", ready && prev ? { week_from: prev, week_to: prev } : null);
  const trend = useTool("business-impact", ready ? { week_from: first, week_to: week } : null);
  const tickets = useTool("tickets", ready ? { week_from: first, week_to: week, group_by: "week" } : null);
  const coverage = useTool("coverage", ready ? { granularity: "week" } : null);
  const bsod = useTool("kpi", ready ? { metric: "bsod_rate", week_from: first, week_to: week } : null);

  if (!impact.data) return <Card><Loading error={impact.error} /></Card>;
  const dt = impact.data.downtime_total;
  const dtp = impactPrev.data?.downtime_total;
  const cost = impact.data.cost_total_zar;
  const top = impact.data.cost_by_driver?.[0];
  const tk = Object.fromEntries((tickets.data?.rows ?? []).map((r) => [r.iso_week, r]));
  const bs = Object.fromEntries((bsod.data?.rows ?? []).map((r) => [r.iso_week, r]));
  const cov = Object.fromEntries((coverage.data?.rows ?? []).map((r) => [`${r.iso_week}|${r.plant}`, r]));
  const byWeek: Record<string, [number, number]> = {};
  for (const r of trend.data?.rows ?? []) {
    if (r.suppressed) continue;
    const a = (byWeek[r.iso_week] ??= [0, 0]);
    a[0] += r.it_app_minutes ?? 0;
    a[1] += r.it_terminal_minutes ?? 0;
  }
  const tw = week ? tk[week]?.tickets : null, tp = prev ? tk[prev]?.tickets : null;
  const wait = (s: { data: unknown }, v: string) => (s.data ? v : "…");     // loading, not missing
  const plants: [string, string][] = [["ROS", "Rosslyn"], ["GQB", "Gqeberha"], ["DBN", "Durban"]];
  const outage: Record<string, string> = {};
  for (const w of impact.data.warnings) if (w.code === "telemetry_outage") outage[w.week] = w.message;

  return (
    <>
      <Card title="What happened and what it cost">
        <div className="hero">
          <div className="label">Estimated cost of IT-caused disruption, {week} (illustrative)</div>
          <div className="figure">{zar(cost.low)} – {zar(cost.high)}</div>
        </div>
        <ul className="headline">
          <li>
            IT faults caused <strong>{pct(dt.it_downtime_share)}</strong> of unplanned line-stop time ({num(dt.it_minutes)} of{" "}
            {num(dt.downtime_minutes)} minutes){dtp ? `, against ${pct(dtp.it_downtime_share)} the week before` : ""}.
          </li>
          {top && <li>Largest cost driver: <strong>{top.driver}</strong> ({zar(top.cost_low_zar)}–{zar(top.cost_high_zar)}).</li>}
          {typeof tw === "number" && (
            <li>
              The service desk logged <strong>{num(tw)}</strong> incidents
              {typeof tp === "number" && tp ? ` (${signedPct((tw - tp) / tp)} vs ${shortWeek(prev!)})` : ""}.
            </li>
          )}
        </ul>
        <div className="tiles">
          <Tile label="IT share of line-stop time" value={pct(dt.it_downtime_share)} sub={dtp ? `${shortWeek(prev!)}: ${pct(dtp.it_downtime_share)}` : undefined} />
          <Tile label="IT line-stop minutes" value={num(dt.it_minutes)} sub={`${num(dt.it_stops)} stops`} />
          <Tile label="Incidents logged" value={wait(tickets, num(tw))} sub={tp ? `${shortWeek(prev!)}: ${num(tp)}` : undefined} />
          <Tile label="Telemetry coverage" value={wait(coverage, pct(cov[`${week}|ALL`]?.coverage))} />
          <Tile label="Blue screens / 1,000 device-weeks" value={wait(bsod, num(bs[week!]?.value, 1))} sub={prev ? `${shortWeek(prev)}: ${num(bs[prev]?.value, 1)}` : undefined} />
        </div>
      </Card>
      <div className="grid2">
        <Card title="IT-caused line-stop minutes">
          <Legend names={["MES client faults", "Station PC faults"]} />
          <Columns labels={upto} highlight={week} unit=" min" series={[
            { name: "MES client faults", values: upto.map((w) => byWeek[w]?.[0] ?? 0) },
            { name: "Station PC faults", values: upto.map((w) => byWeek[w]?.[1] ?? 0) },
          ]} />
          <DataView headers={["Week", "MES client (min)", "Station PC (min)"]}
            rows={upto.map((w) => [w, num(byWeek[w]?.[0] ?? 0), num(byWeek[w]?.[1] ?? 0)])} />
        </Card>
        <Card title="Service desk incidents">
          <Columns labels={upto} highlight={week} series={[{ name: "Incidents", values: upto.map((w) => tk[w]?.tickets) }]} />
          <DataView headers={["Week", "Incidents", "Per 1,000 devices"]}
            rows={upto.map((w) => [w, num(tk[w]?.tickets), num(tk[w]?.tickets_per_1k_devices, 1)])} />
        </Card>
      </div>
      <Card title="Telemetry coverage by plant (shaded: outage week)">
        <Legend names={plants.map(([, n]) => n)} />
        <Lines labels={upto} format={(v) => pct(v)} shade={outage}
          series={plants.map(([c, n]) => ({ name: n, values: upto.map((w) => cov[`${w}|${c}`]?.coverage) }))} />
        <DataView headers={["Week", ...plants.map(([, n]) => n)]}
          rows={upto.map((w) => [w, ...plants.map(([c]) => pct(cov[`${w}|${c}`]?.coverage))])} />
      </Card>
      <Card title="Warnings for this week">
        <Warnings items={impact.data.warnings} />
        <p className="note">Costs use illustrative assumptions (metrics/cost_assumptions.yaml), not finance figures.</p>
      </Card>
    </>
  );
}
