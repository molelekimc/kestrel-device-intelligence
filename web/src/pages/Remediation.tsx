import { Badge, Card, Loading, Table } from "../components/ui";
import { METRIC_NAME, niceDate, signedPct, weekRange } from "../format";
import { useLive, useTool } from "../live";

export default function Remediation() {
  const { status } = useLive();
  const rem = useTool("remediation", {});
  if (!rem.data) return <Card><Loading error={rem.error} /></Card>;
  return (
    <Card title={`What's being done, and did it work (as of ${niceDate(status?.data_to)})`}>
      <p className="note">
        The remediation register is a manual claim. Each action is checked against its change record and telemetry:
        was it implemented, and did the metric it targeted improve compared with before (and with devices it did not
        touch)?
      </p>
      <Table headers={["Action", "Register status", "Actioned", "Verdict", "Evidence"]}
        rows={rem.data.rows.map((r) => {
          const what = r.app ? `${String(r.app).replace(/\.exe$/, "")} crash rate` : METRIC_NAME[r.metric] ?? r.metric;
          const evidence = typeof r.rel_change === "number"
            ? `${what} ${signedPct(r.rel_change)} (${weekRange(r.before_weeks)} → ${weekRange(r.after_weeks)})`
            : r.implementation_evidence ?? r.explanation ?? "–";
          return [<><strong>{r.action_id}</strong> {r.title}</>, r.status, r.actioned_on ? niceDate(r.actioned_on) : "–",
            <Badge kind={r.verdict} />, evidence];
        })} />
    </Card>
  );
}
