import { Badge, Card, Loading, Table } from "../components/ui";
import { METRIC_NAME, niceDateTime, num, signedPct } from "../format";
import { useLive, useTool } from "../live";
import { weekWindow } from "./Overview";

const KIND: Record<string, string> = {
  change: "Change", major_incident: "Major incident", detection: "Detected", problem: "Problem",
  telemetry_outage: "Outage", remediation: "Remediation",
};

export default function Why() {
  const { status, week } = useLive();
  const { prev } = weekWindow(status?.weeks ?? [], week);
  const changes = useTool("changes", week ? { week_from: prev ?? week, week_to: week } : null);
  const timeline = useTool("timeline", week ? { week_from: week, week_to: week } : null);
  if (!changes.data) return <Card><Loading error={changes.error} /></Card>;
  const rows = changes.data.rows.filter((c) => !c.suppressed);
  const strong = rows.filter((c) => c.evidence === "strong" || c.evidence === "moderate");
  const weak = rows.filter((c) => c.evidence === "weak");

  return (
    <>
      <Card title={`Why: logged changes before the shifts (${prev ?? week} – ${week})`}>
        <p className="note">
          Each change is compared on the devices it targeted versus the devices it did not, before and after. This is{" "}
          <strong>evidence, not proof</strong>: “concentrated in” names the app version or bug check code carrying the
          shift, and other changes in the same window are listed so they can be ruled out.
        </p>
        {strong.length ? (
          <Table headers={["Change", "Evidence", "What moved afterwards", "Concentrated in", "Other changes in the window"]}
            rows={strong.map((c) => [
              <><strong>{c.change_id}</strong><br />{c.description}<br /><span className="meta">{niceDateTime(c.actual_start)}</span></>,
              <Badge kind={c.evidence} />,
              `${METRIC_NAME[c.top_metric] ?? c.top_metric} ${signedPct(c.top_rel_change)} on ${num(c.scope_devices)} targeted devices`,
              c.mechanism ?? "–",
              c.competing_changes ?? "none",
            ])} />
        ) : <p>No change in these weeks shows moderate or strong evidence.</p>}
        {weak.length > 0 && (
          <details>
            <summary>Considered, weak evidence ({weak.length})</summary>
            <ul>
              {weak.map((c) => (
                <li key={c.change_id}>
                  <strong>{c.change_id}</strong> {c.description}: {METRIC_NAME[c.top_metric] ?? c.top_metric}{" "}
                  {signedPct(c.top_rel_change)}
                  {c.mechanism_change && !String(c.mechanism_change).includes(c.change_id) ? `, explained by ${c.mechanism_change}` : ""}
                  {!c.has_comparison ? " (no comparison group: targets every device)" : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </Card>
      <Card title={`${week} in order`}>
        {timeline.data ? (
          <ol className="timeline">
            {timeline.data.rows.map((r, i) => (
              <li key={i}>
                <time>{niceDateTime(r.event_at)}</time>
                <span className="kind">{KIND[r.kind] ?? r.kind}</span>
                <span><strong>{r.ref}</strong> {r.title}<br /><span className="meta">{r.plant} · {r.detail}</span></span>
              </li>
            ))}
          </ol>
        ) : <Loading error={timeline.error} />}
      </Card>
    </>
  );
}
