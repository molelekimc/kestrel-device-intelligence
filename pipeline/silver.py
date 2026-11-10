"""Silver: cleaned, typed and conformed to one device key (the standard hostname).

Python cleaning functions are applied to the *distinct* raw values of a column
and joined back as lookup tables, so the rules live in one tested module
(standardise.py) without per-row Python calls. Rows that cannot be conformed go
to quarantine; every rule writes a dq_log line."""

from pipeline import config, dq
from pipeline import standardise as s
from pipeline.reference import LTSC_BUILD

# --------------------------------------------------------------------------- helpers

def _lookup(con, name: str, values_sql: str, fn, out_type: str) -> None:
    """Build TEMP TABLE name(raw, std) from the distinct non-null values of values_sql."""
    raws = [r[0] for r in con.execute(
        f"SELECT DISTINCT v FROM ({values_sql}) WHERE v IS NOT NULL").fetchall()]
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name} (raw VARCHAR, std {out_type})")
    if raws:
        con.execute(
            f"INSERT INTO {name} SELECT unnest($1::VARCHAR[]), unnest($2::{out_type}[])",
            [raws, [fn(r) for r in raws]])


def _macros(con) -> None:
    # ISO 8601 with 'Z' or a +HH:MM offset -> naive UTC timestamp.
    con.execute(r"""
        CREATE OR REPLACE MACRO utc_from_iso(ts) AS
        CASE
          WHEN regexp_full_match(ts, '\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z')
            THEN strptime(left(ts, 19), '%Y-%m-%dT%H:%M:%S')
          WHEN regexp_full_match(ts, '\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d')
            THEN strptime(left(ts, 19), '%Y-%m-%dT%H:%M:%S')
                 - to_minutes((CASE WHEN substr(ts, -6, 1) = '-' THEN -1 ELSE 1 END)
                   * (substr(ts, -5, 2)::INT * 60 + substr(ts, -2, 2)::INT))
        END""")
    con.execute("""
        CREATE OR REPLACE MACRO utc_from_epoch_ms(ms) AS
        make_timestamp(TRY_CAST(ms AS BIGINT) * 1000)""")
    con.execute(f"CREATE OR REPLACE MACRO to_sast(ts) AS ts + to_hours({config.SAST_OFFSET_HOURS})")
    con.execute("CREATE OR REPLACE MACRO iso_week(d) AS strftime(d, '%G-W%V')")
    con.execute(f"""
        CREATE OR REPLACE MACRO valid_duration(x) AS
        x > 0 AND x <= {s.MAX_DURATION_S} AND x NOT IN (99999, 99999000)""")


def _register_builds(con, values_sql: str) -> None:
    _lookup(con, "lk_build", values_sql, s.std_os_build, "VARCHAR")
    _lookup(con, "lk_release", "SELECT std AS v FROM lk_build", s.os_release, "VARCHAR")


# --------------------------------------------------------------------------- intune

def build_intune(con) -> None:
    b = "bronze_intune_devices"
    _lookup(con, "lk_host", f'SELECT "Device name" AS v FROM {b}', s.std_hostname, "VARCHAR")
    _lookup(con, "lk_plant", f'SELECT "Device category" AS v FROM {b}', s.plant_from_category, "VARCHAR")
    _lookup(con, "lk_compliance", f'SELECT "Compliance" AS v FROM {b}', s.std_compliance, "VARCHAR")
    _lookup(con, "lk_ts", f'SELECT "Last check-in" AS v FROM {b} UNION SELECT "Enrollment date" FROM {b}',
            s.parse_intune_datetime, "TIMESTAMP")
    _lookup(con, "lk_model", f"SELECT coalesce(Manufacturer, '') || '|' || Model AS v FROM {b}",
            lambda v: s.std_model(*v.split("|", 1)), "VARCHAR")
    _lookup(con, "lk_storage", f'SELECT "Total storage" AS v FROM {b}', s.storage_gb, "INTEGER")
    _register_builds(con, f'SELECT "OS version" AS v FROM {b}')

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg_intune AS
        SELECT b._source_file AS source_file, b._row_no AS source_row,
               h.std                                   AS device_key,
               lower(trim(b."Azure AD Device ID"))     AS aad_device_id,
               trim(b."Device ID")                     AS intune_device_id,
               p.std                                   AS category_plant,
               trim(b."Managed by")                    AS managed_by,
               c.std                                   AS compliance,
               ob.std                                  AS intune_os_build,
               lc.std                                  AS last_checkin_at,
               en.std                                  AS enrolled_at,
               nullif(upper(trim(b."Serial number")), '') AS serial,
               nullif(trim(b.Manufacturer), '')        AS manufacturer,
               coalesce(m.std, 'Unknown')              AS model,
               nullif(trim(b."Primary user UPN"), '') IS NOT NULL AS has_primary_user,
               sg.std                                  AS storage_gb,
               b."Device name" AS raw_device_name, b."Device category" AS raw_category,
               b."Compliance" AS raw_compliance, b."OS version" AS raw_os_version,
               b."Last check-in" AS raw_last_checkin, b."Enrollment date" AS raw_enrolled,
               b.Manufacturer AS raw_manufacturer, b.Model AS raw_model,
               b."Total storage" AS raw_storage
        FROM {b} b
        LEFT JOIN lk_host h        ON h.raw = b."Device name"
        LEFT JOIN lk_plant p       ON p.raw = b."Device category"
        LEFT JOIN lk_compliance c  ON c.raw = b."Compliance"
        LEFT JOIN lk_build ob      ON ob.raw = b."OS version"
        LEFT JOIN lk_ts lc         ON lc.raw = b."Last check-in"
        LEFT JOIN lk_ts en         ON en.raw = b."Enrollment date"
        LEFT JOIN lk_model m       ON m.raw = coalesce(b.Manufacturer, '') || '|' || b.Model
        LEFT JOIN lk_storage sg    ON sg.raw = b."Total storage" """)

    t = "stg_intune"
    dq.log_count(con, "intune_hostname_case_whitespace", t, 'raw_device_name <> device_key',
                 "trimmed and upper-cased to PPP-CC-NNNNN")
    dq.log_count(con, "intune_plant_category_alias", t,
                 "category_plant IS NOT NULL AND trim(raw_category) <> category_plant",
                 "mapped free-text category to plant code")
    dq.log_count(con, "intune_plant_category_missing", t, "category_plant IS NULL",
                 "plant taken from hostname prefix", "warning")
    dq.log_count(con, "intune_plant_category_conflict", t,
                 "category_plant IS NOT NULL AND category_plant <> substr(device_key, 1, 3)",
                 "hostname prefix wins over manual category", "warning")
    dq.log_count(con, "intune_date_non_iso", t,
                 "NOT regexp_full_match(raw_last_checkin, '\\d{4}-.*') "
                 "OR NOT regexp_full_match(raw_enrolled, '\\d{4}-.*')",
                 "parsed: AM/PM = M/D/Y, 24h slash = D/M/Y (SAST)")
    dq.log_count(con, "intune_compliance_vocab", t,
                 "compliance IS NOT NULL AND trim(raw_compliance) <> compliance",
                 "case-normalised")
    dq.log_count(con, "intune_compliance_missing", t, "compliance IS NULL",
                 "set to NULL (blank, 'null' or 'N/A')", "warning")
    dq.log_count(con, "intune_os_version_no_prefix", t, "raw_os_version NOT LIKE '10.0.%'",
                 "prefixed with 10.0.")
    dq.log_count(con, "intune_model_vendor_prefix", t,
                 "model <> 'Unknown' AND trim(raw_model) <> model AND length(trim(raw_model)) <> 10",
                 "manufacturer prefix removed")
    dq.log_count(con, "intune_model_lenovo_mtm", t,
                 "raw_manufacturer = 'Lenovo' AND regexp_full_match(trim(raw_model), '[0-9A-Z]{10}')",
                 "machine type mapped to model name")
    dq.log_count(con, "intune_model_missing", t, "model = 'Unknown'",
                 "model set to 'Unknown'", "warning")
    dq.log_count(con, "intune_storage_units", t,
                 "raw_storage IS DISTINCT FROM storage_gb::VARCHAR || ' GB'",
                 "normalised to nominal GB (GiB values converted)")

    bad = "device_key IS NULL OR aad_device_id IS NULL OR aad_device_id = '' OR enrolled_at IS NULL OR last_checkin_at IS NULL"
    n = dq.quarantine(con, "intune",
                      f"SELECT source_file, source_row, to_json(x) AS raw_record FROM {t} x WHERE {bad}",
                      "unconformable_identity_or_dates", retained=False)
    dq.log(con, "intune_unconformable", t, n, "quarantined", "warning" if n else "info")

    # One hostname can carry several enrolment records after a re-enrolment.
    # The latest enrolment is the device; older records keep their AAD id mapping
    # so their telemetry still joins.
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_intune_enrollment AS
        SELECT device_key, aad_device_id, intune_device_id, substr(device_key, 1, 3) AS plant_code,
               substr(device_key, 5, 2) AS device_class, category_plant, managed_by, compliance,
               intune_os_build, last_checkin_at, enrolled_at, serial, manufacturer, model,
               has_primary_user, storage_gb, source_file, source_row,
               row_number() OVER (PARTITION BY device_key
                                  ORDER BY enrolled_at DESC, last_checkin_at DESC) = 1 AS is_current
        FROM {t} WHERE NOT ({bad})""")
    dq.log_count(con, "intune_reenrolled_superseded", "silver_intune_enrollment", "NOT is_current",
                 "older enrolment kept for ID mapping, excluded from silver_device")
    dq.log_count(con, "intune_hostname_new_serial", "silver_intune_enrollment",
                 "device_key IN (SELECT device_key FROM silver_intune_enrollment "
                 "GROUP BY 1 HAVING count(DISTINCT serial) > 1) AND is_current",
                 "hardware swapped under the same hostname; latest enrolment wins", "warning")

    stale_before = f"TIMESTAMP '{config.SNAPSHOT_AT}' - INTERVAL {config.STALE_AFTER_DAYS} DAY"
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_device AS
        SELECT e.device_key, e.plant_code, e.device_class, e.aad_device_id, e.serial,
               e.manufacturer, e.model, e.intune_os_build,
               coalesce(r.std, 'Unknown') AS intune_os_release,
               split_part(e.intune_os_build, '.', 3) = '{LTSC_BUILD}' AS on_win10_build,
               e.compliance, e.managed_by, e.has_primary_user, e.storage_gb,
               e.enrolled_at, e.last_checkin_at,
               CASE WHEN e.last_checkin_at < {stale_before} THEN 'stale_retired' ELSE 'active' END AS lifecycle
        FROM silver_intune_enrollment e
        LEFT JOIN lk_release r ON r.raw = e.intune_os_build
        WHERE e.is_current""")
    dq.log_count(con, "intune_stale_retired", "silver_device", "lifecycle = 'stale_retired'",
                 f"no check-in for {config.STALE_AFTER_DAYS}+ days before snapshot; "
                 "counted as Intune-active only up to last check-in")

    con.execute("""
        CREATE OR REPLACE TABLE silver_device_id_map AS
        SELECT aad_device_id, device_key, is_current FROM silver_intune_enrollment""")


# --------------------------------------------------------------------------- telemetry

def build_telemetry(con) -> None:
    con.execute(r"""
        CREATE OR REPLACE TEMP TABLE tel_files AS
        SELECT DISTINCT _source_file AS f,
               regexp_extract(_source_file, '_(\d{8})', 1) AS file_day,
               _source_file LIKE '%\_resend%' ESCAPE '\' AS is_resend
        FROM bronze_telemetry_lines""")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE tel_raw AS
        SELECT l._source_file AS source_file, l._row_no AS source_row, l.raw_line,
               json_valid(l.raw_line) AS is_json, f.is_resend,
               (NOT f.is_resend AND f.file_day IN (SELECT file_day FROM tel_files WHERE is_resend))
                   AS superseded
        FROM bronze_telemetry_lines l JOIN tel_files f ON f.f = l._source_file""")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE tel_parsed AS
        WITH j AS (
            SELECT *, CASE WHEN is_json THEN raw_line::JSON END AS j FROM tel_raw
        ), p AS (
            SELECT *,
                   CASE WHEN j->>'event_id' IS NOT NULL THEN '2.3'
                        WHEN j->>'eventId'  IS NOT NULL THEN '2.4' END AS schema_version,
                   coalesce(j->'payload', j->'data') AS pl
            FROM j
        )
        SELECT source_file, source_row, raw_line, is_json, is_resend, superseded, schema_version,
               coalesce(j->>'event_id', j->>'eventId')               AS event_id,
               coalesce(j->>'event_type', j->>'eventType')           AS event_type_raw,
               coalesce(j->>'agent_version', j->>'agentVersion')     AS agent_version,
               lower(trim(coalesce(j->'device'->>'deviceId', j->>'deviceId'), '{} ')) AS aad_device_id,
               coalesce(j->'device'->>'computer_name', j->>'fqdn')   AS host_raw,
               CASE schema_version
                 WHEN '2.3' THEN utc_from_iso(j->>'ts')
                 WHEN '2.4' THEN utc_from_epoch_ms(j->>'timestamp') END AS event_ts_utc,
               coalesce(pl->>'os_build', pl->>'osBuild')             AS os_build_raw,
               CASE schema_version
                 WHEN '2.3' THEN TRY_CAST(pl->>'boot_duration_ms' AS DOUBLE) / 1000
                 ELSE TRY_CAST(pl->>'bootDurationSec' AS DOUBLE) END AS boot_s_raw,
               CASE schema_version
                 WHEN '2.3' THEN TRY_CAST(pl->>'logon_duration_ms' AS DOUBLE) / 1000
                 ELSE TRY_CAST(pl->>'logonDurationSec' AS DOUBLE) END AS logon_s_raw,
               coalesce(pl->>'app_name', pl->>'appName')             AS app_name,
               coalesce(pl->>'app_version', pl->>'appVersion')       AS app_version,
               coalesce(pl->>'bugcheck_code', pl->>'bugCheckCode')   AS bugcheck_code
        FROM p""")

    # Reject reasons are evaluated in order; a row gets the first that applies.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tel_stage1 AS
        SELECT *,
               CASE WHEN event_type_raw IN ('startup', 'Startup') THEN 'startup'
                    WHEN event_type_raw IN ('app_crash', 'AppCrash') THEN 'app_crash'
                    WHEN event_type_raw IN ('bugcheck', 'BugCheck') THEN 'bugcheck' END AS event_type,
               CASE WHEN NOT is_json THEN 'malformed_json'
                    WHEN superseded THEN 'superseded_by_resend'
                    WHEN schema_version IS NULL OR event_id IS NULL THEN 'unknown_schema'
                    WHEN event_type_raw NOT IN ('startup', 'Startup', 'app_crash', 'AppCrash',
                                                'bugcheck', 'BugCheck') THEN 'unknown_event_type'
                    WHEN event_ts_utc IS NULL THEN 'bad_timestamp'
                    WHEN aad_device_id IS NULL OR aad_device_id = '' THEN 'missing_device_id'
               END AS reject_reason
        FROM tel_parsed""")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tel_stage2 AS
        SELECT t.*, m.device_key,
               CASE WHEN t.reject_reason IS NOT NULL THEN t.reject_reason
                    WHEN row_number() OVER (PARTITION BY t.reject_reason IS NULL, t.event_id
                         ORDER BY t.is_resend DESC, t.source_file, t.source_row) > 1
                         THEN 'duplicate_event_id'
                    WHEN m.device_key IS NULL THEN 'orphan_device'
                    WHEN to_sast(t.event_ts_utc)::DATE NOT BETWEEN DATE '{config.WINDOW_START}'
                         AND DATE '{config.WINDOW_END}' THEN 'outside_reporting_window'
               END AS final_reason
        FROM tel_stage1 t
        LEFT JOIN silver_device_id_map m ON m.aad_device_id = t.aad_device_id""")

    actions = {
        "malformed_json": ("quarantined (truncated or invalid JSON line)", "warning"),
        "superseded_by_resend": ("original day file replaced by its _resend file", "info"),
        "unknown_schema": ("quarantined (no recognisable event id)", "warning"),
        "unknown_event_type": ("quarantined", "warning"),
        "bad_timestamp": ("quarantined", "warning"),
        "missing_device_id": ("quarantined", "warning"),
        "duplicate_event_id": ("duplicate removed, first copy kept", "info"),
        "orphan_device": ("quarantined: device id not in any Intune record", "warning"),
        "outside_reporting_window": ("quarantined", "info"),
    }
    for reason, (action, severity) in actions.items():
        n = dq.quarantine(con, "telemetry",
                          f"SELECT source_file, source_row, raw_line AS raw_record FROM tel_stage2 "
                          f"WHERE final_reason = '{reason}'", reason, retained=False)
        dq.log(con, f"telemetry_{reason}", "bronze_telemetry_lines", n, action,
               severity if n else "info")

    con.execute("CREATE OR REPLACE TEMP TABLE tel_ok AS SELECT * FROM tel_stage2 WHERE final_reason IS NULL")
    _register_builds(con, "SELECT os_build_raw AS v FROM tel_ok")
    _lookup(con, "lk_tel_host", "SELECT host_raw AS v FROM tel_ok", s.std_hostname, "VARCHAR")

    con.execute("""
        CREATE OR REPLACE TABLE silver_telemetry_event AS
        SELECT t.event_id, t.device_key, t.aad_device_id, t.event_type,
               t.event_ts_utc, to_sast(t.event_ts_utc) AS event_ts_sast,
               to_sast(t.event_ts_utc)::DATE AS event_date, iso_week(to_sast(t.event_ts_utc)) AS iso_week,
               t.schema_version, t.agent_version, b.std AS os_build, r.std AS os_release,
               CASE WHEN valid_duration(t.boot_s_raw)  THEN t.boot_s_raw  END AS boot_s,
               CASE WHEN valid_duration(t.logon_s_raw) THEN t.logon_s_raw END AS logon_s,
               t.app_name, t.app_version, t.bugcheck_code, h.std AS reported_hostname,
               t.source_file, t.source_row
        FROM tel_ok t
        LEFT JOIN lk_build b    ON b.raw = t.os_build_raw
        LEFT JOIN lk_release r  ON r.raw = b.std
        LEFT JOIN lk_tel_host h ON h.raw = t.host_raw""")

    for col in ("boot", "logon"):
        n = dq.quarantine(con, "telemetry",
                          f"SELECT source_file, source_row, raw_line AS raw_record FROM tel_ok "
                          f"WHERE event_type = 'startup' AND {col}_s_raw IS NOT NULL "
                          f"AND NOT valid_duration({col}_s_raw)",
                          f"impossible_{col}_duration", retained=True)
        dq.log(con, f"telemetry_impossible_{col}_duration", "silver_telemetry_event", n,
               "duration nulled (<=0, 99999 sentinel or >600 s); startup still counted",
               "warning" if n else "info")

    e = "silver_telemetry_event"
    dq.log_count(con, "telemetry_schema_2_4_normalised", e, "schema_version = '2.4'",
                 "agent 2.4 fields renamed, seconds kept, epoch ms converted to UTC")
    dq.log_count(con, "telemetry_schema_2_3_normalised", e, "schema_version = '2.3'",
                 "agent 2.3 durations converted ms -> s, ISO timestamps converted to UTC")
    dq.log_count(con, "telemetry_ts_with_offset", "tel_ok",
                 "schema_version = '2.3' AND raw_line::JSON->>'ts' NOT LIKE '%Z'",
                 "+02:00 offset timestamps converted to UTC")
    dq.log_count(con, "telemetry_superseded_aad_id", e,
                 "aad_device_id IN (SELECT aad_device_id FROM silver_device_id_map WHERE NOT is_current)",
                 "event from a pre-re-enrolment device id mapped to its current device")
    dq.log_count(con, "telemetry_hostname_mismatch", e,
                 "reported_hostname IS DISTINCT FROM device_key",
                 "Intune hostname (via device id) kept as device key", "warning")
    dq.log_count(con, "telemetry_os_build_missing", e, "os_build IS NULL",
                 "event kept; build falls back to Intune in Gold", "warning")
    for label, cond, sev, action in (
            ("shopfloor", "=", "info", "expected (kiosk auto-logon); excluded from logon percentiles"),
            ("other", "<>", "warning", "excluded from logon percentiles")):
        dq.log_count(con, f"telemetry_logon_missing_{label}", "tel_ok",
                     f"event_type = 'startup' AND logon_s_raw IS NULL AND substr(device_key, 5, 2) {cond} 'SF'",
                     action, sev)


# --------------------------------------------------------------------------- tracker

def build_tracker(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE trk AS
        SELECT b.*, b._row_no > (SELECT min(_row_no) FROM bronze_tracker WHERE trim(c0) = 'Hostname')
                    AS is_body
        FROM bronze_tracker b""")
    _lookup(con, "lk_trk_host", "SELECT c0 AS v FROM trk WHERE is_body", s.std_hostname, "VARCHAR")
    _lookup(con, "lk_trk_plant", "SELECT c1 AS v FROM trk WHERE is_body", s.plant_from_category, "VARCHAR")
    _lookup(con, "lk_wave", "SELECT c2 AS v FROM trk WHERE is_body", s.std_wave, "VARCHAR")
    _lookup(con, "lk_date", "SELECT c3 AS v FROM trk WHERE is_body", s.parse_tracker_date, "DATE")
    _lookup(con, "lk_status", "SELECT c4 AS v FROM trk WHERE is_body", s.std_status, "VARCHAR")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE trk_stage AS
        SELECT t._source_file AS source_file, t._row_no AS source_row,
               to_json(struct_pack(c0, c1, c2, c3, c4, c5, c6)) AS raw_record,
               h.std AS device_key, p.std AS tracker_plant, w.std AS wave, d.std AS scheduled_date,
               st.std AS status, nullif(trim(t.c5), '') AS updated_by, nullif(trim(t.c6), '') AS comments,
               t.c0, t.c1, t.c2, t.c3, t.c4,
               CASE WHEN NOT t.is_body THEN 'title_or_header_row'
                    WHEN coalesce(trim(t.c0), '') = '' AND coalesce(trim(t.c4), '') = '' THEN 'blank_row'
                    WHEN upper(trim(t.c0)) = 'TOTAL' THEN 'footer_total_row'
                    WHEN h.std IS NULL THEN 'unparseable_hostname'
               END AS reject_reason
        FROM trk t
        LEFT JOIN lk_trk_host h  ON h.raw = t.c0
        LEFT JOIN lk_trk_plant p ON p.raw = t.c1
        LEFT JOIN lk_wave w      ON w.raw = t.c2
        LEFT JOIN lk_date d      ON d.raw = t.c3
        LEFT JOIN lk_status st   ON st.raw = t.c4""")

    # A rescheduled device appears twice: the old 'Rescheduled' row and the new
    # wave's row. Keep the non-rescheduled, latest-scheduled row.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE trk_ranked AS
        SELECT *, row_number() OVER (PARTITION BY device_key
                     ORDER BY status = 'Rescheduled', scheduled_date DESC NULLS LAST, source_row DESC) AS rn
        FROM trk_stage WHERE reject_reason IS NULL""")

    for reason in ("title_or_header_row", "blank_row", "footer_total_row", "unparseable_hostname"):
        n = dq.quarantine(con, "tracker",
                          f"SELECT source_file, source_row, raw_record FROM trk_stage WHERE reject_reason = '{reason}'",
                          reason, retained=False)
        dq.log(con, f"tracker_{reason}", "bronze_tracker", n, "quarantined",
               "warning" if reason == "unparseable_hostname" and n else "info")
    n = dq.quarantine(con, "tracker",
                      "SELECT source_file, source_row, raw_record FROM trk_ranked WHERE rn > 1",
                      "superseded_duplicate_host", retained=False)
    dq.log(con, "tracker_duplicate_host", "bronze_tracker", n,
           "older row (usually 'Rescheduled') superseded by the device's latest row", "warning")

    con.execute("""
        CREATE OR REPLACE TABLE silver_rollout_tracker AS
        SELECT device_key, substr(device_key, 1, 3) AS plant_code, tracker_plant,
               coalesce(wave, 'Unknown') AS wave, scheduled_date, coalesce(status, 'Unknown') AS status,
               updated_by, comments, source_row
        FROM trk_ranked WHERE rn = 1""")

    t, sv = "trk_ranked", "silver_rollout_tracker"
    dq.log_count(con, "tracker_hostname_typo", t,
                 "rn = 1 AND upper(trim(c0)) <> device_key",
                 "hyphens restored / letter O corrected to zero", "warning")
    dq.log_count(con, "tracker_plant_alias", t, "rn = 1 AND trim(c1) = 'Port Elizabeth'",
                 "Port Elizabeth mapped to Gqeberha (GQB)")
    dq.log_count(con, "tracker_plant_conflict", sv,
                 "tracker_plant IS NOT NULL AND tracker_plant <> plant_code",
                 "hostname prefix wins", "warning")
    dq.log_count(con, "tracker_wave_vocab", t, "rn = 1 AND trim(c2) <> wave",
                 "normalised to Pilot / Wave 1-3 / Exempt / TBC")
    dq.log_count(con, "tracker_status_vocab", t, "rn = 1 AND trim(c4) <> status",
                 "normalised (done/Complete/Completed -> Done etc.)")
    dq.log_count(con, "tracker_wave_or_status_unknown", sv, "wave = 'Unknown' OR status = 'Unknown'",
                 "kept as 'Unknown'", "warning")
    dq.log_count(con, "tracker_date_non_iso", t,
                 "rn = 1 AND scheduled_date IS NOT NULL AND NOT regexp_full_match(trim(c3), '\\d{4}-\\d\\d-\\d\\d')",
                 "parsed DD/MM/YYYY, Excel serial and 'DD Mon' (2026) dates")
    dq.log_count(con, "tracker_date_unparseable", t,
                 "rn = 1 AND scheduled_date IS NULL AND coalesce(trim(c3), '') <> ''",
                 "scheduled_date set NULL", "warning")
    dq.log_count(con, "tracker_host_not_in_intune", sv,
                 "device_key NOT IN (SELECT device_key FROM silver_device)",
                 "kept; reconciliation status 'not_in_intune'", "warning")
    dq.log_count(con, "intune_device_not_in_tracker", "silver_device",
                 "device_key NOT IN (SELECT device_key FROM silver_rollout_tracker)",
                 "kept; tracker wave 'Not tracked'", "warning")


def build(con) -> None:
    _macros(con)
    build_intune(con)
    build_telemetry(con)
    build_tracker(con)
