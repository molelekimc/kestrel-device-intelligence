"""Validate the Silver and Gold device layers against answer_key/device_truth.csv.

This is the only module besides evals/questions.yaml that reads the answer key."""

import duckdb

from pipeline import config

TRUTH = config.ROOT / "answer_key" / "device_truth.csv"

# Truth wave 0 means "not in the rollout"; the tracker spells that several ways.
WAVE_MATCH = """
    CASE t.rollout_wave
      WHEN '0' THEN coalesce(s.wave, 'Not tracked') IN ('Exempt', 'TBC', 'Not tracked')
      WHEN '1' THEN s.wave IN ('Pilot', 'Wave 1')
      WHEN '2' THEN s.wave = 'Wave 2'
      WHEN '3' THEN s.wave = 'Wave 3'
    END"""


def _rate(con, sql) -> dict:
    hit, n = con.execute(sql).fetchone()
    return {"matched": int(hit or 0), "of": int(n), "rate": round((hit or 0) / n, 4) if n else None}


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or config.DB_PATH), read_only=True)
    con.execute(f"CREATE TEMP TABLE truth AS SELECT * FROM read_csv('{TRUTH}', all_varchar = true)")
    con.execute("CREATE TEMP VIEW t AS SELECT * FROM truth WHERE is_lab_device = 'False'")
    j = "FROM t JOIN silver_device d ON d.device_key = t.hostname"
    model_truth = "regexp_replace(t.model, '^(Lenovo|HP|Dell|Advantech) ', '')"
    r = {}

    r["devices_found"] = _rate(con, "SELECT count(d.device_key), count(*) FROM t LEFT JOIN silver_device d ON d.device_key = t.hostname")
    r["extra_devices_not_in_truth"] = con.execute(
        "SELECT count(*) FROM silver_device WHERE device_key NOT IN (SELECT hostname FROM t)").fetchone()[0]
    r["extra_devices_all_retired"] = con.execute(
        "SELECT bool_and(lifecycle = 'stale_retired') FROM silver_device WHERE device_key NOT IN (SELECT hostname FROM t)"
    ).fetchone()[0]
    r["azure_ad_id"] = _rate(con, f"SELECT count(*) FILTER (WHERE lower(t.aad_device_id) = d.aad_device_id), count(*) {j}")
    r["plant"] = _rate(con, f"SELECT count(*) FILTER (WHERE d.plant_code = t.plant), count(*) {j}")
    r["device_class"] = _rate(con, f"SELECT count(*) FILTER (WHERE d.device_class = t.device_class), count(*) {j}")
    r["model_all"] = _rate(con, f"SELECT count(*) FILTER (WHERE d.model = {model_truth}), count(*) {j}")
    r["model_when_known"] = _rate(con, f"SELECT count(*) FILTER (WHERE d.model = {model_truth}), count(*) {j} WHERE d.model <> 'Unknown'")
    r["model_unknown_in_intune"] = con.execute(f"SELECT count(*) {j} WHERE d.model = 'Unknown'").fetchone()[0]
    r["ltsc_flag"] = _rate(con, f"SELECT count(*) FILTER (WHERE d.on_win10_build = (t.is_ltsc = 'True')), count(*) {j}")
    r["lab_devices_quarantined_as_orphans"] = _rate(con, """
        SELECT count(*) FILTER (WHERE q.aad IS NOT NULL), count(*)
        FROM truth LEFT JOIN (
            SELECT DISTINCT lower(trim(coalesce(json_extract_string(raw_record, '$.device.deviceId'),
                                               json_extract_string(raw_record, '$.deviceId')), '{} ')) AS aad
            FROM quarantine WHERE reason = 'orphan_device') q ON q.aad = lower(truth.aad_device_id)
        WHERE truth.is_lab_device = 'True'""")

    # Migration: telemetry-observed 25H2 vs the true migration date (window ends 25 Oct).
    g = "FROM t JOIN gold_rollout_reconciliation g ON g.device_key = t.hostname"
    migrated = f"(t.migrated_date IS NOT NULL AND t.migrated_date <= '{config.WINDOW_END}')"
    r["migration_observed_precision"] = _rate(con, f"""
        SELECT count(*) FILTER (WHERE {migrated}), count(*) {g} WHERE g.first_target_seen IS NOT NULL""")
    r["migration_observed_recall"] = _rate(con, f"""
        SELECT count(*) FILTER (WHERE g.first_target_seen IS NOT NULL), count(*) {g} WHERE {migrated}""")
    r["migration_missed_after_last_telemetry"] = con.execute(f"""
        SELECT count(*) {g} WHERE {migrated} AND g.first_target_seen IS NULL
          AND g.last_seen < t.migrated_date::DATE""").fetchone()[0]
    r["failed_marked_done_caught"] = _rate(con, f"""
        SELECT count(*) FILTER (WHERE g.reconciliation_status = 'claimed_not_observed'), count(*)
        {g} WHERE g.tracker_status = 'Done' AND NOT {migrated}""")
    r["claimed_not_observed_precision"] = _rate(con, f"""
        SELECT count(*) FILTER (WHERE NOT {migrated}), count(*) {g} WHERE g.reconciliation_status = 'claimed_not_observed'""")
    r["tracker_wave_agrees_with_truth"] = _rate(con, f"""
        SELECT count(*) FILTER (WHERE {WAVE_MATCH}), count(*) FROM t LEFT JOIN silver_rollout_tracker s ON s.device_key = t.hostname""")

    # Storyline B mechanism: LTSC devices go silent once they receive agent 2.4.
    r["ltsc_silent_after_agent_24"] = _rate(con, """
        SELECT count(*) FILTER (WHERE last_seen < t.agent_24_date::DATE + 1 OR last_seen IS NULL), count(*)
        FROM t LEFT JOIN (SELECT device_key, max(event_date) AS last_seen FROM silver_telemetry_event GROUP BY 1) e
          ON e.device_key = t.hostname
        WHERE t.is_ltsc = 'True' AND t.agent_24_date IS NOT NULL AND t.agent_24_date <= '2026-10-25'""")
    _business(con, r)
    con.close()
    return r


BUSINESS = config.ROOT / "answer_key" / "business_truth"


def _business(con, r: dict) -> None:
    """Business Silver/Gold (Phase 6) against the business generator's truth files."""
    con.execute(f"""CREATE TEMP VIEW tt AS SELECT * FROM read_csv('{BUSINESS}/ticket_truth.csv', all_varchar = true)
                    WHERE is_stale_duplicate_row = 'False'""")
    con.execute(f"CREATE TEMP VIEW dtt AS SELECT * FROM read_csv('{BUSINESS}/downtime_truth.csv', all_varchar = true)")
    con.execute(f"CREATE TEMP VIEW rt AS SELECT * FROM read_csv('{BUSINESS}/remediation_truth.csv', all_varchar = true)")
    r["tickets_found"] = _rate(con, "SELECT count(s.number), count(*) FROM tt LEFT JOIN silver_ticket s USING (number)")
    r["ticket_device_when_ci_given"] = _rate(con, """
        SELECT count(*) FILTER (WHERE s.device_key = t.device), count(*)
        FROM tt t JOIN silver_ticket s USING (number)
        JOIN (SELECT DISTINCT number FROM bronze_itsm_incidents WHERE coalesce(trim(cmdb_ci), '') <> '') b USING (number)
        WHERE t.device <> ''""")
    r["ticket_invalid_time_flagged"] = _rate(con, """
        SELECT count(*) FILTER (WHERE coalesce(s.time_invalid, false) = (t.resolved_before_opened = 'True')), count(*)
        FROM tt t JOIN silver_ticket s USING (number)""")
    r["downtime_duplicates_removed"] = _rate(con, """
        SELECT count(*) FILTER (WHERE s.event_id IS NULL), count(*) FROM dtt d
        LEFT JOIN silver_downtime s USING (event_id) WHERE d.is_duplicate = 'True'""")
    r["downtime_it_attribution"] = _rate(con, """
        SELECT count(*) FILTER (WHERE s.is_it = (d.it_or_non_it = 'IT')), count(*)
        FROM dtt d JOIN silver_downtime s USING (event_id)""")
    r["downtime_it_recoded_other_recovered"] = _rate(con, """
        SELECT count(*) FILTER (WHERE s.is_it), count(*) FROM dtt d JOIN silver_downtime s USING (event_id)
        WHERE d.recoded_as_other = 'true' OR d.recoded_as_other = 'True'""")
    r["downtime_minutes_within_1"] = _rate(con, """
        SELECT count(*) FILTER (WHERE abs(s.minutes - d.true_minutes::DOUBLE) <= 1), count(*)
        FROM dtt d JOIN silver_downtime s USING (event_id)""")
    r["remediation_verdicts"] = _rate(con, """
        SELECT count(*) FILTER (WHERE g.verdict = rt.true_verdict), count(*)
        FROM rt JOIN gold_remediation_effect g USING (action_id)""")


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
