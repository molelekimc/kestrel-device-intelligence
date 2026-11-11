import { useEffect, useState } from "react";
import { refreshHistory, type Row } from "../api";
import { Badge, Card, Loading, Table, Warnings } from "../components/ui";
import { ago, niceDateTime, num } from "../format";
import { useLive, useTool } from "../live";

export default function DataTrust() {
  const { week, version } = useLive();
  const fresh = useTool("freshness", {});
  const dq = useTool("data-quality", week ? { week } : null);
  const [runs, setRuns] = useState<Row[] | null>(null);
  useEffect(() => {
    refreshHistory().then((r) => setRuns(r.runs)).catch(() => setRuns([]));
  }, [version]);

  return (
    <>
      <Card title="Data freshness against SLA">
        {fresh.data ? (
          <Table headers={["Feed", "Status", "As of", "Age", "SLA", "Why"]}
            rows={fresh.data.rows.map((r) => [r.label, <Badge kind={r.status} />, niceDateTime(r.as_of),
              r.age_hours === null ? "–" : `${num(r.age_hours, 1)} h`, `${r.expected_hours} h + ${r.grace_hours} h`, r.reason])} />
        ) : <Loading error={fresh.error} />}
        <p className="note">
          Telemetry is also checked per plant: a plant whose latest weekday carried under half its normal devices is
          stale even while files keep arriving. That is how a site outage is caught on the first morning.
        </p>
      </Card>
      <Card title="Refresh history">
        {runs === null ? <Loading /> : runs.length === 0 ? (
          <p className="note">No refresh runs logged yet. Start the refresh watcher (see the README) to rebuild on new data.</p>
        ) : (
          <Table headers={["Run", "Status", "Trigger", "Data as of", "Finished", "Duration", "Files", "Behind SLA"]}
            rows={runs.map((r) => [r.run_id, <Badge kind={r.status} />, r.trigger, niceDateTime(r.as_of), ago(r.finished_at),
              `${num(r.duration_s, 1)} s`, num(r.changed_count), (r.warehouse?.not_fresh ?? []).join(", ") || (r.error ?? "–")])} />
        )}
      </Card>
      <Card title={`Data quality warnings, ${week ?? ""}`}>
        {dq.data ? <Warnings items={dq.data.warnings} /> : <Loading error={dq.error} />}
        {dq.data && (
          <details>
            <summary>Pipeline checks that changed or held back data ({dq.data.rows.length})</summary>
            <Table headers={["Check", "Rows", "Action", "Severity"]}
              rows={dq.data.rows.map((r) => [r.check_name, num(r.rows_affected), r.action_taken, <Badge kind={r.severity} />])} />
          </details>
        )}
      </Card>
    </>
  );
}
