"""Gold: device-week facts, weekly KPIs, coverage, rollout reconciliation,
slice warnings and threshold breaches. Device-grain tables stay inside the
warehouse; the MCP server only ever returns aggregates computed from them."""

from itertools import combinations

from pipeline import config, dq
from pipeline.metrics import definitions, kpi_sql
from pipeline import standardise
from pipeline.reference import TARGET_RELEASE
from pipeline.silver import _lookup

CUBE_DIMS = ("plant_code", "device_class", "model", "os_release")


def _weeks(con) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_week AS
        SELECT strftime(d, '%G-W%V') AS iso_week, d::DATE AS week_start, (d + INTERVAL 6 DAY)::DATE AS week_end
        FROM range(DATE '{config.WINDOW_START}', DATE '{config.WINDOW_END}' + INTERVAL 1 DAY, INTERVAL 7 DAY) t(d)""")


def _device_week(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE ev_week AS
        SELECT device_key, iso_week,
               count(*)                                         AS events,
               count(*) FILTER (WHERE event_type = 'startup')   AS startups,
               count(boot_s)                                    AS valid_boots,
               count(logon_s)                                   AS valid_logons,
               count(*) FILTER (WHERE event_type = 'app_crash') AS app_crashes,
               count(*) FILTER (WHERE event_type = 'bugcheck')  AS bugchecks,
               arg_max(os_build, event_ts_utc)                  AS last_build,
               arg_max(agent_version, event_ts_utc)             AS agent_version
        FROM silver_telemetry_event GROUP BY ALL""")

    _lookup(con, "lk_release", "SELECT intune_os_build AS v FROM silver_device "
            "UNION SELECT os_build FROM silver_telemetry_event", standardise.os_release, "VARCHAR")

    # A device that did not report in a week keeps the last build it was seen on;
    # one never seen falls back to its Intune build.
    con.execute("""
        CREATE OR REPLACE TABLE gold_device_week AS
        WITH grid AS (
            SELECT d.*, w.iso_week, w.week_start, w.week_end,
                   (d.enrolled_at::DATE <= w.week_end AND d.last_checkin_at::DATE >= w.week_start) AS intune_active
            FROM silver_device d CROSS JOIN gold_week w
            WHERE d.enrolled_at::DATE <= w.week_end
        ), filled AS (
            SELECT g.*, e.events, e.startups, e.valid_boots, e.valid_logons, e.app_crashes, e.bugchecks,
                   e.agent_version,
                   coalesce(last_value(e.last_build IGNORE NULLS) OVER (
                       PARTITION BY g.device_key ORDER BY g.iso_week
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), g.intune_os_build) AS os_build
            FROM grid g LEFT JOIN ev_week e USING (device_key, iso_week)
        )
        SELECT f.device_key, f.iso_week, f.week_start, f.plant_code, f.device_class, f.model,
               f.os_build, coalesce(r.std, 'Unknown') AS os_release,
               f.on_win10_build AS is_ltsc,
               f.intune_active, coalesce(f.events, 0) > 0 AS reporting,
               coalesce(f.startups, 0) AS startups, coalesce(f.valid_boots, 0) AS valid_boots,
               coalesce(f.valid_logons, 0) AS valid_logons, coalesce(f.app_crashes, 0) AS app_crashes,
               coalesce(f.bugchecks, 0) AS bugchecks, f.agent_version,
               coalesce(t.wave, 'Not tracked') AS tracker_wave, coalesce(t.status, 'Not tracked') AS tracker_status
        FROM filled f
        LEFT JOIN lk_release r ON r.raw = f.os_build
        LEFT JOIN silver_rollout_tracker t USING (device_key)
        WHERE f.intune_active OR coalesce(f.events, 0) > 0""")
    dq.log_count(con, "gold_reporting_while_not_intune_active", "gold_device_week",
                 "reporting AND NOT intune_active",
                 "kept as reporting; excluded from coverage numerator", "warning")

    con.execute("""
        CREATE OR REPLACE TABLE gold_event_fact AS
        SELECT device_key, iso_week, event_date, event_type, os_release AS event_os_release, boot_s, logon_s,
               app_name, app_version, bugcheck_code
        FROM silver_telemetry_event""")


def _kpi_weekly(con) -> None:
    parts = []
    for k in range(len(CUBE_DIMS) + 1):
        for dims in combinations(CUBE_DIMS, k):
            sql, params = kpi_sql(["iso_week", *dims])
            labels = ", ".join(f"{d}" if d in dims else f"'ALL' AS {d}" for d in CUBE_DIMS)
            parts.append((f"SELECT iso_week, {labels}, * EXCLUDE (iso_week{''.join(', ' + d for d in dims)}) "
                          f"FROM ({sql})", params))
    con.execute("CREATE OR REPLACE TABLE gold_kpi_weekly AS " + " UNION ALL BY NAME ".join(p for p, _ in parts),
                [x for _, ps in parts for x in ps])


def _coverage_daily(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE gold_coverage_daily AS
        WITH days AS (
            SELECT d::DATE AS day FROM range(DATE '%s', DATE '%s' + INTERVAL 1 DAY, INTERVAL 1 DAY) t(d)
        ), active AS (
            SELECT day, dv.device_key, dv.plant_code, dv.device_class
            FROM days JOIN silver_device dv
              ON dv.enrolled_at::DATE <= day AND dv.last_checkin_at::DATE >= day
        ), rep AS (
            SELECT DISTINCT event_date AS day, device_key FROM silver_telemetry_event
        )
        SELECT a.day, strftime(a.day, '%%G-W%%V') AS iso_week, isodow(a.day) <= 5 AS is_weekday,
               coalesce(a.plant_code, 'ALL') AS plant_code, coalesce(a.device_class, 'ALL') AS device_class,
               count(*) AS intune_active_devices,
               count(r.device_key) AS reporting_devices,
               count(r.device_key) / count(*) AS coverage
        FROM active a LEFT JOIN rep r USING (day, device_key)
        GROUP BY GROUPING SETS ((a.day), (a.day, a.plant_code), (a.day, a.device_class),
                                (a.day, a.plant_code, a.device_class))""" % (config.WINDOW_START, config.WINDOW_END))
    # Devices do not all boot every day, so daily coverage is naturally well below
    # weekly coverage. Outages are judged against each slice's typical weekday.
    con.execute("""
        CREATE OR REPLACE TABLE gold_coverage_daily AS
        SELECT *, reporting_devices / nullif(median(reporting_devices) FILTER (WHERE is_weekday)
                      OVER (PARTITION BY plant_code, device_class), 0) AS reporting_vs_weekday_median
        FROM gold_coverage_daily""")


def _rollout(con) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_rollout_reconciliation AS
        WITH tel AS (
            SELECT device_key,
                   min(event_date) FILTER (WHERE os_release = '{TARGET_RELEASE}') AS first_target_seen,
                   max(event_date) AS last_seen,
                   arg_max(os_release, event_ts_utc) AS last_release
            FROM silver_telemetry_event GROUP BY 1
        ), base AS (
            SELECT coalesce(d.device_key, t.device_key) AS device_key,
                   coalesce(d.plant_code, t.plant_code) AS plant_code, d.device_class,
                   coalesce(d.on_win10_build, false) AS is_ltsc,
                   d.device_key IS NOT NULL AS in_intune,
                   coalesce(t.wave, 'Not tracked') AS tracker_wave,
                   coalesce(t.status, 'Not tracked') AS tracker_status,
                   t.scheduled_date, tel.first_target_seen, tel.last_seen, tel.last_release,
                   d.intune_os_release
            FROM silver_device d
            FULL JOIN silver_rollout_tracker t USING (device_key)
            LEFT JOIN tel ON tel.device_key = coalesce(d.device_key, t.device_key)
        )
        SELECT *,
          CASE
            WHEN NOT in_intune THEN 'not_in_intune'
            WHEN last_seen IS NULL THEN 'no_telemetry'
            WHEN tracker_status = 'Exempt' OR is_ltsc THEN
                 CASE WHEN first_target_seen IS NOT NULL THEN 'exempt_but_upgraded' ELSE 'exempt_ok' END
            WHEN first_target_seen IS NOT NULL AND last_release <> '{TARGET_RELEASE}' THEN 'rolled_back_observed'
            WHEN tracker_status = 'Done' AND last_release = '{TARGET_RELEASE}' THEN 'confirmed'
            WHEN tracker_status = 'Done' AND last_seen < scheduled_date THEN 'unverifiable_stale_telemetry'
            WHEN tracker_status = 'Done' THEN 'claimed_not_observed'
            WHEN last_release = '{TARGET_RELEASE}' THEN 'observed_not_claimed'
            ELSE 'consistent_not_upgraded'
          END AS reconciliation_status
        FROM base""")
    con.execute("""
        CREATE OR REPLACE TABLE gold_rollout_summary AS
        SELECT plant_code, tracker_wave, tracker_status, reconciliation_status, count(*) AS devices
        FROM gold_rollout_reconciliation GROUP BY ALL""")
    dq.log_count(con, "rollout_claimed_not_observed", "gold_rollout_reconciliation",
                 "reconciliation_status = 'claimed_not_observed'",
                 "tracker says Done but telemetry still shows the old build", "warning")


def _warnings(con) -> None:
    rules = definitions()["warning_rules"]
    con.execute("""
        CREATE OR REPLACE TABLE gold_slice_warnings (
            iso_week VARCHAR, plant_code VARCHAR, device_class VARCHAR,
            code VARCHAR, severity VARCHAR, value DOUBLE, message VARCHAR)""")
    k = """FROM gold_kpi_weekly WHERE model = 'ALL' AND os_release = 'ALL'"""
    lc, cc = rules["low_coverage"], rules["critical_coverage"]
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        SELECT iso_week, plant_code, device_class,
               CASE WHEN coverage < {cc['threshold']} THEN 'critical_coverage' ELSE 'low_coverage' END,
               CASE WHEN coverage < {cc['threshold']} THEN '{cc['severity']}' ELSE '{lc['severity']}' END,
               coverage,
               printf('Telemetry coverage %.0f%% (%d of %d Intune-active devices reporting); KPIs describe only the reporting devices.',
                      100 * coverage, covered_device_weeks, intune_active_device_weeks)
        {k} AND coverage < {lc['threshold']}""")
    cd = rules["coverage_drop"]
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        SELECT iso_week, plant_code, device_class, 'coverage_drop', '{cd['severity']}', delta,
               printf('Coverage fell %.0f points vs the previous week (%.0f%% -> %.0f%%); week-on-week KPI changes may reflect which devices report.',
                      -100 * delta, 100 * prev, 100 * coverage)
        FROM (SELECT *, coverage - lag(coverage) OVER (PARTITION BY plant_code, device_class ORDER BY iso_week) AS delta,
                     lag(coverage) OVER (PARTITION BY plant_code, device_class ORDER BY iso_week) AS prev
              {k})
        WHERE delta <= -{cd['threshold_pts']}""")
    od = rules["outage_day"]
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        SELECT iso_week, plant_code, 'ALL', 'telemetry_outage', '{od['severity']}', count(*),
               printf('Telemetry outage: %d weekday(s) with reporting devices below %.0f%% of a normal weekday (%s). Volumes and rates for this week are unreliable.',
                      count(*), 100 * {od['threshold']}, string_agg(strftime(day, '%a %d %b'), ', ' ORDER BY day))
        FROM gold_coverage_daily
        WHERE is_weekday AND device_class = 'ALL' AND reporting_vs_weekday_median < {od['threshold']}
        GROUP BY iso_week, plant_code""")
    ms = rules["cohort_mix_shift"]
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        WITH mix AS (
            SELECT iso_week, coalesce(plant_code, 'ALL') AS plant_code,
                   avg(is_ltsc::INT) AS ltsc_share
            FROM gold_device_week WHERE reporting
            GROUP BY GROUPING SETS ((iso_week), (iso_week, plant_code))
        ), d AS (
            SELECT *, ltsc_share - lag(ltsc_share) OVER (PARTITION BY plant_code ORDER BY iso_week) AS delta FROM mix
        )
        SELECT iso_week, plant_code, 'ALL', 'cohort_mix_shift', '{ms['severity']}', delta,
               printf('Reporting cohort changed: Win10 LTSC shop-floor share of reporting devices moved %+.0f points vs the previous week. Compare like-for-like before reading a trend.',
                      100 * delta)
        FROM d WHERE abs(delta) >= {ms['threshold_pts']}""")
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        SELECT iso_week, 'ALL', 'ALL', 'agent_schema_mix', '{rules['agent_schema_mix']['severity']}',
               avg((schema_version = '2.4')::INT),
               printf('Telemetry agent upgrade in progress: %.0f%% of events from agent 2.4, rest from 2.3. Both schemas are normalised to seconds and UTC.',
                      100 * avg((schema_version = '2.4')::INT))
        FROM silver_telemetry_event GROUP BY iso_week
        HAVING count(DISTINCT schema_version) > 1""")
    con.execute(rf"""
        INSERT INTO gold_slice_warnings
        SELECT DISTINCT strftime(strptime(regexp_extract(source_file, '_(\d{{8}})', 1), '%Y%m%d'), '%G-W%V'),
               'ALL', 'ALL', 'resend_applied', '{rules['resend_applied']['severity']}', NULL,
               'A daily telemetry file was re-sent (' || source_file || '); the resend replaced the partial original.'
        FROM quarantine WHERE reason = 'superseded_by_resend' """)
    im = rules["impossible_durations"]
    con.execute(f"""
        INSERT INTO gold_slice_warnings
        WITH q AS (
            SELECT e.iso_week, count(*) FILTER (WHERE e.boot_s IS NULL) AS bad, count(*) AS n
            FROM silver_telemetry_event e WHERE e.event_type = 'startup' GROUP BY 1
        )
        SELECT iso_week, 'ALL', 'ALL', 'impossible_durations', '{im['severity']}', bad / n,
               printf('%d startup(s) (%.1f%%) had impossible boot durations and are excluded from boot percentiles.', bad, 100 * bad / n)
        FROM q WHERE bad / n >= {im['threshold']}""")


def _breaches(con) -> None:
    th = definitions()["thresholds"]
    metrics = definitions()["metrics"]
    min_devices = definitions()["min_group_devices"]
    unions = []
    for dim in (None, *CUBE_DIMS):
        others = " AND ".join(f"{d} = 'ALL'" for d in CUBE_DIMS if d != dim)
        dim_filter = f"{dim} <> 'ALL'" if dim else "true"
        unions.append(f"""
            SELECT iso_week, '{dim or 'fleet'}' AS slice_dimension,
                   {dim if dim else "'ALL'"} AS slice_value, * EXCLUDE (iso_week)
            FROM gold_kpi_weekly WHERE {others} AND {dim_filter}""")
    con.execute("CREATE OR REPLACE TEMP TABLE slices AS " + " UNION ALL ".join(unions))
    con.execute("""
        CREATE OR REPLACE TABLE gold_threshold_breaches (
            iso_week VARCHAR, previous_week VARCHAR, slice_dimension VARCHAR, slice_value VARCHAR,
            metric VARCHAR, previous_value DOUBLE, value DOUBLE, change DOUBLE, change_type VARCHAR,
            direction VARCHAR, devices_reporting BIGINT, events BIGINT)""")
    event_col = {"bsod_rate": "bugchecks", "app_crash_rate": "app_crashes"}
    for metric, rule in th.items():
        direction = metrics[metric]["direction"]
        events = event_col.get(metric, "startups")
        if "pct_change" in rule:
            change, cond, ctype = "(cur - prev) / nullif(prev, 0)", f"abs(change) >= {rule['pct_change']}", "pct"
        else:
            change, cond, ctype = "cur - prev", f"change <= {rule['abs_change']}", "abs"
        min_events = rule.get("min_events", 0)
        con.execute(f"""
            INSERT INTO gold_threshold_breaches
            SELECT iso_week, prev_week, slice_dimension, slice_value, '{metric}', prev, cur, change, '{ctype}',
                   CASE WHEN '{direction}' = 'lower_is_better' AND change > 0 THEN 'worse'
                        WHEN '{direction}' = 'higher_is_better' AND change < 0 THEN 'worse'
                        ELSE 'better' END,
                   devices_reporting, ev
            FROM (
                SELECT *, {change} AS change FROM (
                    SELECT iso_week, slice_dimension, slice_value, devices_reporting, {events} AS ev,
                           {metric} AS cur,
                           lag({metric}) OVER w AS prev, lag(iso_week) OVER w AS prev_week,
                           lag(devices_reporting) OVER w AS prev_devices,
                           lag({events}) OVER w AS prev_ev
                    FROM slices WINDOW w AS (PARTITION BY slice_dimension, slice_value ORDER BY iso_week))
                WHERE prev IS NOT NULL AND devices_reporting >= {min_devices} AND prev_devices >= {min_devices}
                  AND greatest(ev, prev_ev) >= {min_events})
            WHERE {cond}""")


def build(con) -> None:
    _weeks(con)
    _device_week(con)
    _kpi_weekly(con)
    _coverage_daily(con)
    _rollout(con)
    _warnings(con)
    _breaches(con)
