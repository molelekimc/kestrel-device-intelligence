"""Business Silver (Phase 6): service desk, changes and their target devices, MES lines and
stops, and the remediation register, conformed to the same device key as the device layer.

Free text that can name people (ticket callers and descriptions, change and action owners)
stays in restricted tables (silver_ticket_text) or in Bronze; nothing downstream reads it."""

from pipeline import config, dq
from pipeline import standardise as s
from pipeline.metrics import definitions
from pipeline.reference import MES_PLANTS
from pipeline.silver import _lookup


def _sla_case() -> str:
    sla = definitions()["business"]["sla_resolve_minutes"]
    return "CASE priority " + " ".join(f"WHEN {p} THEN {m}" for p, m in sla.items()) + " END"


# --------------------------------------------------------------------------- tickets

def build_tickets(con) -> None:
    b = "bronze_itsm_incidents"
    ts_cols = ("opened_at", "first_response_at", "resolved_at", "closed_at", "sys_updated_on")
    _lookup(con, "lk_b_ts", " UNION ".join(f"SELECT {c} AS v FROM {b}" for c in ts_cols),
            s.parse_local_datetime, "TIMESTAMP")
    _lookup(con, "lk_b_pri", f"SELECT priority AS v FROM {b}", s.std_priority, "INTEGER")
    _lookup(con, "lk_b_cat", f"SELECT category AS v FROM {b}", s.std_ticket_category, "VARCHAR")
    _lookup(con, "lk_b_ci", f"SELECT cmdb_ci AS v FROM {b}", s.std_hostname, "VARCHAR")
    _lookup(con, "lk_b_loc", f"SELECT location AS v FROM {b}", s.plant_from_category, "VARCHAR")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg_ticket AS
        SELECT b._source_file AS source_file, b._row_no AS source_row, trim(b.number) AS number,
               o.std AS opened_std, a.std AS ack_std, r.std AS resolved_std, c.std AS closed_std, u.std AS updated_std,
               trim(b.state) AS state_raw, p.std AS priority_std, cat.std AS category_std,
               ci.std AS ci_key, loc.std AS location_plant,
               trim(b.assignment_group) AS assignment_group_raw,
               nullif(trim(b.parent_incident), '') AS parent_raw, nullif(trim(b.problem_id), '') AS problem_raw,
               nullif(trim(b.caused_by_change), '') AS change_raw, nullif(trim(b.close_code), '') AS close_raw,
               TRY_CAST(b.reopen_count AS INTEGER) AS reopen_std,
               b.priority AS raw_priority, b.category AS raw_category, b.cmdb_ci AS raw_ci,
               b.location AS raw_location, b.caller_id, b.short_description
        FROM {b} b
        LEFT JOIN lk_b_ts o ON o.raw = b.opened_at
        LEFT JOIN lk_b_ts a ON a.raw = b.first_response_at
        LEFT JOIN lk_b_ts r ON r.raw = b.resolved_at
        LEFT JOIN lk_b_ts c ON c.raw = b.closed_at
        LEFT JOIN lk_b_ts u ON u.raw = b.sys_updated_on
        LEFT JOIN lk_b_pri p ON p.raw = b.priority
        LEFT JOIN lk_b_cat cat ON cat.raw = b.category
        LEFT JOIN lk_b_ci ci ON ci.raw = b.cmdb_ci
        LEFT JOIN lk_b_loc loc ON loc.raw = b.location""")

    t = "stg_ticket"
    q = lambda where: f"SELECT source_file, source_row, to_json(x) AS raw_record FROM {t} x WHERE {where}"
    n = dq.quarantine(con, "itsm", q("opened_std IS NULL OR number IS NULL OR number = ''"),
                      "unparseable_opened_at_or_number", retained=False)
    dq.log(con, "itsm_unparseable", t, n, "quarantined", "warning" if n else "info")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg_ticket_ranked AS
        SELECT *, row_number() OVER (PARTITION BY number ORDER BY updated_std DESC NULLS LAST, source_row DESC) AS rn
        FROM {t} WHERE opened_std IS NOT NULL AND number IS NOT NULL AND number <> ''""")
    n = dq.quarantine(con, "itsm", "SELECT source_file, source_row, to_json(x) AS raw_record "
                                   "FROM stg_ticket_ranked x WHERE rn > 1", "stale_reassignment_row", retained=False)
    dq.log(con, "itsm_duplicate_number", "bronze_itsm_incidents", n,
           "incident exported more than once (reassigned); kept the row with the latest sys_updated_on", "warning")

    r = "stg_ticket_ranked"
    dq.log_count(con, "itsm_priority_vocab", r, "rn = 1 AND priority_std IS NOT NULL AND trim(raw_priority) NOT LIKE '_ - %'",
                 "normalised to 1-4 (P1 / High / 2 - High ...)")
    dq.log_count(con, "itsm_priority_unknown", r, "rn = 1 AND priority_std IS NULL", "priority set NULL", "warning")
    dq.log_count(con, "itsm_category_vocab", r, "rn = 1 AND category_std IS NOT NULL AND trim(raw_category) <> category_std",
                 "free-text category mapped to a standard category")
    dq.log_count(con, "itsm_category_unknown", r, "rn = 1 AND category_std IS NULL", "category set 'Other'", "warning")
    dq.log_count(con, "itsm_ci_blank", r, "rn = 1 AND coalesce(trim(raw_ci), '') = ''",
                 "no device; plant taken from location", "warning")
    dq.log_count(con, "itsm_ci_standardised", r, "rn = 1 AND ci_key IS NOT NULL AND trim(raw_ci) <> ci_key",
                 "FQDN / case / missing hyphen fixed")
    dq.log_count(con, "itsm_ci_not_in_intune", r,
                 "rn = 1 AND ci_key IS NOT NULL AND ci_key NOT IN (SELECT device_key FROM silver_device)",
                 "device not in Intune; ticket kept without a device", "warning")

    n = dq.quarantine(con, "itsm", "SELECT source_file, source_row, to_json(x) AS raw_record FROM stg_ticket_ranked x "
                                   "WHERE rn = 1 AND resolved_std < opened_std", "resolved_before_opened", retained=True)
    dq.log(con, "itsm_resolved_before_opened", "bronze_itsm_incidents", n,
           "ticket kept; resolution time set NULL (excluded from MTTR)", "warning" if n else "info")

    con.execute(f"""
        CREATE OR REPLACE TABLE silver_ticket AS
        WITH base AS (
            SELECT t.number, t.opened_std AS opened_at, t.ack_std AS acknowledged_at,
                   CASE WHEN t.resolved_std >= t.opened_std THEN t.resolved_std END AS resolved_at,
                   t.resolved_std < t.opened_std AS time_invalid,
                   t.state_raw AS state, t.priority_std AS priority, coalesce(t.category_std, 'Other') AS category,
                   t.assignment_group_raw AS assignment_group, t.parent_raw AS parent_incident,
                   t.problem_raw AS problem_id, t.change_raw AS change_id, t.close_raw AS close_code,
                   coalesce(t.reopen_std, 0) AS reopen_count,
                   d.device_key, d.device_class, coalesce(d.plant_code, t.location_plant) AS plant_code,
                   t.source_file, t.source_row
            FROM stg_ticket_ranked t LEFT JOIN silver_device d ON d.device_key = t.ci_key
            WHERE t.rn = 1
        )
        SELECT *, parent_incident IS NOT NULL AS is_child,
               number IN (SELECT parent_incident FROM base WHERE parent_incident IS NOT NULL) AS is_major,
               opened_at::DATE AS opened_date, strftime(opened_at, '%G-W%V') AS iso_week,
               date_diff('second', opened_at, acknowledged_at) / 60.0 AS mtta_min,
               date_diff('second', opened_at, resolved_at) / 60.0 AS mttr_min,
               {_sla_case()} AS sla_resolve_min,
               CASE WHEN resolved_at IS NOT NULL
                    THEN date_diff('second', opened_at, resolved_at) / 60.0 > {_sla_case()} END AS sla_breached
        FROM base""")
    dq.log_count(con, "itsm_plant_unknown", "silver_ticket", "plant_code IS NULL",
                 "no device and no recognisable location", "warning")
    dq.log_count(con, "itsm_child_tickets", "silver_ticket", "is_child",
                 "child of a major incident; excluded from MTTR (closed with the parent)")

    con.execute("""
        CREATE OR REPLACE TABLE silver_ticket_text AS
        SELECT number, caller_id, short_description FROM stg_ticket_ranked WHERE rn = 1""")


# --------------------------------------------------------------------------- changes and groups

def build_changes(con) -> None:
    b = "bronze_change_requests"
    _lookup(con, "lk_c_ts", " UNION ".join(f"SELECT {c} AS v FROM {b}" for c in
                                           ("planned_start", "planned_end", "actual_start", "actual_end")),
            s.parse_local_datetime, "TIMESTAMP")
    _lookup(con, "lk_c_grp", f"SELECT assignment_target AS v FROM {b} UNION SELECT group_name FROM bronze_group_membership",
            s.std_group, "VARCHAR")
    _lookup(con, "lk_m_host", "SELECT member_name AS v FROM bronze_group_membership", s.std_hostname, "VARCHAR")

    con.execute(f"""
        CREATE OR REPLACE TABLE silver_change AS
        SELECT trim(b.number) AS change_id, trim(b.type) AS change_type, trim(b.category) AS category,
               trim(b.short_description) AS description, g.std AS target_group,
               ps.std AS planned_start, ast.std AS actual_start, aen.std AS actual_end,
               trim(b.state) AS state, nullif(trim(b.close_code), '') AS close_code,
               nullif(trim(b.problem_id), '') AS problem_id,
               (ast.std IS NOT NULL AND coalesce(b.close_code, '') NOT ILIKE '%rolled back%'
                AND b.state NOT IN ('Cancelled', 'Scheduled')) AS implemented,
               coalesce(b.close_code, '') ILIKE '%rolled back%' AS rolled_back,
               b.assignment_target AS raw_target
        FROM {b} b
        LEFT JOIN lk_c_grp g ON g.raw = b.assignment_target
        LEFT JOIN lk_c_ts ps ON ps.raw = b.planned_start
        LEFT JOIN lk_c_ts ast ON ast.raw = b.actual_start
        LEFT JOIN lk_c_ts aen ON aen.raw = b.actual_end""")
    c = "silver_change"
    dq.log_count(con, "change_target_group_drift", c, "trim(raw_target) <> target_group",
                 "group name normalised (case, spaces, trailing space)")
    dq.log_count(con, "change_not_implemented", c, "NOT implemented",
                 "cancelled, scheduled or rolled back: no scope impact measured")
    dq.log_count(con, "change_after_window", c, f"planned_start > TIMESTAMP '{config.WINDOW_END} 23:59:59'",
                 "scheduled after the data window")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE stg_member AS
        SELECT m._source_file AS source_file, m._row_no AS source_row, g.std AS group_name, h.std AS member_key,
               m.member_name AS raw_name, (h.std IN (SELECT device_key FROM silver_device)) AS in_intune
        FROM bronze_group_membership m
        LEFT JOIN lk_c_grp g ON g.raw = m.group_name
        LEFT JOIN lk_m_host h ON h.raw = m.member_name""")
    n = dq.quarantine(con, "groups", "SELECT source_file, source_row, to_json(x) AS raw_record FROM stg_member x "
                                     "WHERE member_key IS NULL OR NOT in_intune", "member_not_in_intune", retained=False)
    dq.log(con, "groups_stale_member", "bronze_group_membership", n,
           "member not an Intune device (retired or unparseable); excluded", "warning" if n else "info")
    dq.log_count(con, "groups_member_name_standardised", "stg_member",
                 "member_key IS NOT NULL AND trim(raw_name) <> member_key", "case / whitespace fixed")
    con.execute("""
        CREATE OR REPLACE TABLE silver_group_member AS
        SELECT DISTINCT group_name, member_key AS device_key FROM stg_member WHERE in_intune""")
    con.execute("""
        CREATE OR REPLACE TABLE silver_change_scope AS
        SELECT c.change_id, m.device_key FROM silver_change c
        JOIN silver_group_member m ON m.group_name = c.target_group""")
    dq.log_count(con, "change_target_unresolved", c,
                 "target_group NOT IN (SELECT group_name FROM silver_group_member)",
                 "target group has no members in the membership export", "warning")


# --------------------------------------------------------------------------- MES

def build_mes(con) -> None:
    plants = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in MES_PLANTS.items())
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_line AS
        SELECT upper(trim(line_id)) AS line_id, CASE upper(trim(mes_plant)) {plants} END AS plant_code,
               trim(site_name) AS site_name, trim(product_family) AS product_family,
               TRY_CAST(standard_units_per_hour AS INTEGER) AS units_per_hour, trim(shift_pattern) AS shift_pattern
        FROM bronze_mes_lines""")
    dq.log_count(con, "mes_site_name_alias", "bronze_mes_lines",
                 "trim(site_name) NOT IN ('Rosslyn Assembly', 'PE Components', 'Prospecton Seating')",
                 "site typed by hand; plant taken from the MES plant code")

    _lookup(con, "lk_term", "SELECT terminal_name AS v FROM bronze_mes_terminals", s.std_hostname, "VARCHAR")
    con.execute("""
        CREATE OR REPLACE TABLE silver_line_terminal AS
        SELECT upper(trim(t.line_id)) AS line_id, upper(trim(t.station)) AS station, h.std AS device_key,
               upper(trim(t.line_critical)) = 'Y' AS line_critical, t.terminal_name AS raw_name
        FROM bronze_mes_terminals t LEFT JOIN lk_term h ON h.raw = t.terminal_name""")
    dq.log_count(con, "mes_terminal_name_standardised", "silver_line_terminal",
                 "device_key IS NOT NULL AND trim(raw_name) <> device_key", "case / FQDN / hyphen fixed")
    dq.log_count(con, "mes_terminal_not_in_intune", "silver_line_terminal",
                 "device_key IS NULL OR device_key NOT IN (SELECT device_key FROM silver_device)",
                 "station kept without a device", "warning")

    b = "bronze_mes_downtime"
    _lookup(con, "lk_dt_ts", f"SELECT start_local AS v FROM {b} UNION SELECT end_local FROM {b}",
            s.parse_local_datetime, "TIMESTAMP")
    _lookup(con, "lk_dt_line", f"SELECT mes_plant || '|' || line AS v FROM {b}",
            lambda v: s.std_line_id(*v.split("|", 1)), "VARCHAR")
    _lookup(con, "lk_dt_it", f"SELECT coalesce(reason_code, '') || '|' || coalesce(operator_comment, '') AS v FROM {b}",
            lambda v: (lambda t: t[0] or "")(s.downtime_it_type(*v.split("|", 1))), "VARCHAR")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg_stop AS
        SELECT b._source_file AS source_file, b._row_no AS source_row, trim(b.event_id) AS event_id,
               CASE upper(trim(b.mes_plant)) {plants} END AS plant_code, l.std AS line_id,
               upper(nullif(trim(b.station), '')) AS station, st.std AS start_at, en.std AS end_at,
               TRY_CAST(b.duration_min AS DOUBLE) AS typed_minutes, upper(trim(b.reason_code)) AS reason_code,
               nullif(it.std, '') AS it_type, trim(b.shift) AS shift, b.operator_comment, b.line AS raw_line
        FROM {b} b
        LEFT JOIN lk_dt_ts st ON st.raw = b.start_local
        LEFT JOIN lk_dt_ts en ON en.raw = b.end_local
        LEFT JOIN lk_dt_line l ON l.raw = b.mes_plant || '|' || b.line
        LEFT JOIN lk_dt_it it ON it.raw = coalesce(b.reason_code, '') || '|' || coalesce(b.operator_comment, '')""")
    q = lambda where: f"SELECT source_file, source_row, to_json(x) AS raw_record FROM stg_stop x WHERE {where}"
    bad = "line_id IS NULL OR start_at IS NULL OR end_at IS NULL OR end_at < start_at"
    n = dq.quarantine(con, "mes", q(bad), "unparseable_line_or_times", retained=False)
    dq.log(con, "mes_stop_unparseable", b, n, "quarantined", "warning" if n else "info")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg_stop_ranked AS
        SELECT *, date_diff('second', start_at, end_at) / 60.0 AS minutes,
               row_number() OVER (PARTITION BY line_id, coalesce(station, ''), start_at, reason_code
                                  ORDER BY end_at, event_id) AS rn
        FROM stg_stop WHERE NOT ({bad})""")
    n = dq.quarantine(con, "mes", "SELECT source_file, source_row, to_json(x) AS raw_record FROM stg_stop_ranked x "
                                  "WHERE rn > 1", "duplicate_stop", retained=False)
    dq.log(con, "mes_stop_duplicate", b, n, "same line, station, start and reason logged twice; first kept",
           "warning" if n else "info")
    r = "stg_stop_ranked"
    dq.log_count(con, "mes_line_written_short", r, "rn = 1 AND upper(trim(raw_line)) <> line_id",
                 "line id rebuilt from MES plant + line text")
    dq.log_count(con, "mes_duration_keyed_in_hours", r,
                 "rn = 1 AND abs(typed_minutes - minutes) > 2 AND abs(typed_minutes * 60 - minutes) <= 2",
                 "typed duration was in hours; system start/end used")
    dq.log_count(con, "mes_duration_mismatch", r,
                 "rn = 1 AND abs(typed_minutes - minutes) > 2 AND abs(typed_minutes * 60 - minutes) > 2",
                 "typed duration disagrees with start/end; system start/end used", "warning")
    dq.log_count(con, "mes_other_reclassified_it", r, "rn = 1 AND reason_code = 'OTHER' AND it_type IS NOT NULL",
                 "OTHER stop whose comment names a PC, screen, reboot or MES fault counted as IT")

    con.execute(f"""
        CREATE OR REPLACE TABLE silver_downtime AS
        SELECT s.event_id, s.line_id, coalesce(s.plant_code, l.plant_code) AS plant_code, s.station,
               t.device_key, coalesce(t.line_critical, false) AS line_critical,
               s.start_at, s.end_at, s.minutes, s.reason_code,
               s.it_type IS NOT NULL AS is_it, s.it_type,
               s.reason_code = 'OTHER' AND s.it_type IS NOT NULL AS it_reclassified,
               s.shift, strftime(s.start_at, '%G-W%V') AS iso_week, s.start_at::DATE AS stop_date
        FROM stg_stop_ranked s
        LEFT JOIN silver_line l ON l.line_id = s.line_id
        LEFT JOIN silver_line_terminal t ON t.line_id = s.line_id AND t.station = s.station
        WHERE s.rn = 1""")
    dq.log_count(con, "mes_it_stop_without_terminal", "silver_downtime", "is_it AND device_key IS NULL",
                 "IT stop whose station is not in the terminal map", "warning")


# --------------------------------------------------------------------------- remediation register

def build_remediation(con) -> None:
    b = "bronze_remediation"
    _lookup(con, "lk_r_day", f"SELECT date_raised AS v FROM {b} UNION SELECT due_date FROM {b} "
                             f"UNION SELECT date_actioned FROM {b}", s.parse_day, "DATE")
    _lookup(con, "lk_r_status", f"SELECT status AS v FROM {b}", s.std_remediation_status, "VARCHAR")
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_remediation AS
        SELECT trim(b.action_id) AS action_id, nullif(trim(b.problem_id), '') AS problem_id, trim(b.title) AS title,
               nullif(trim(b.linked_change), '') AS change_id, trim(b.target) AS target,
               coalesce(st.std, 'Unknown') AS status, rd.std AS raised_on, dd.std AS due_on, ad.std AS actioned_on,
               CASE upper(trim(b.verified)) WHEN 'Y' THEN true WHEN 'N' THEN false END AS verified,
               trim(b.expected_benefit) AS expected_benefit, b.status AS raw_status
        FROM {b} b
        LEFT JOIN lk_r_status st ON st.raw = b.status
        LEFT JOIN lk_r_day rd ON rd.raw = b.date_raised
        LEFT JOIN lk_r_day dd ON dd.raw = b.due_date
        LEFT JOIN lk_r_day ad ON ad.raw = b.date_actioned""")
    r = "silver_remediation"
    dq.log_count(con, "remediation_status_vocab", r, "trim(raw_status) <> status", "status normalised")
    dq.log_count(con, "remediation_no_linked_change", r, "change_id IS NULL",
                 "no change linked; implementation cannot be checked against a change record", "warning")
    dq.log_count(con, "remediation_done_unverified", r, "status = 'Done' AND verified IS DISTINCT FROM true",
                 "marked done without verification", "warning")


def build(con) -> None:
    build_tickets(con)
    build_changes(con)
    build_mes(con)
    build_remediation(con)
