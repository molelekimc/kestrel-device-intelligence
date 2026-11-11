import { Columns } from "../components/charts";
import { Card, DataView, Loading, Table } from "../components/ui";
import { num, pct } from "../format";
import { useLive, useTool } from "../live";
import { weekWindow } from "./Overview";

export default function ServiceDesk() {
  const { status, week } = useLive();
  const { first, upto } = weekWindow(status?.weeks ?? [], week);
  const byCat = useTool("tickets", week ? { week_from: week, week_to: week, group_by: "category" } : null);
  const byPri = useTool("tickets", week ? { week_from: week, week_to: week, group_by: "priority" } : null);
  const bsod = useTool("tickets", week && first ? { week_from: first, week_to: week, category: "Display / blue screen", group_by: "week" } : null);
  if (!byCat.data) return <Card><Loading error={byCat.error} /></Card>;
  const n = (r: Record<string, any>) => (typeof r.tickets === "number" ? r.tickets : 0);
  const row = (label: string, r: Record<string, any>) => [label, r.suppressed ? r.tickets : num(r.tickets), num(r.mtta_p50_min),
    num(r.mttr_p50_hours, 1), pct(r.sla_breach_rate), pct(r.reopen_rate)];
  const bw = Object.fromEntries((bsod.data?.rows ?? []).map((r) => [r.iso_week, r]));
  return (
    <>
      <Card title={`Service desk, ${week}`}>
        <Table headers={["Category", "Incidents", "Median acknowledge (min)", "Median resolve (h)", "Missed SLA", "Reopened"]}
          rows={[...byCat.data.rows].sort((a, b) => n(b) - n(a)).map((r) => row(r.category, r))} />
        {byPri.data && (
          <Table caption="By priority" headers={["Priority", "Incidents", "Median acknowledge (min)", "Median resolve (h)", "Missed SLA", "Reopened"]}
            rows={byPri.data.rows.map((r) => row(`P${r.priority}`, r))} />
        )}
        <p className="note">
          Tickets show what people report. Silent failures, such as devices that stop sending telemetry, raise none.
          Resolution figures exclude child tickets of a major incident.
        </p>
      </Card>
      <Card title="Blue screen incidents per week">
        <Columns labels={upto} highlight={week} series={[{ name: "Blue screen incidents", values: upto.map((w) => bw[w]?.tickets) }]} />
        <DataView headers={["Week", "Incidents", "Median resolve (h)", "Missed SLA"]}
          rows={upto.map((w) => [w, num(bw[w]?.tickets), num(bw[w]?.mttr_p50_hours, 1), pct(bw[w]?.sla_breach_rate)])} />
      </Card>
    </>
  );
}
