"""Business Gold (Phase 6): ticket and line-stop facts, illustrative cost, change-impact
evidence, remediation verdicts and a dated timeline. Device-grain rows stay in the warehouse;
the MCP tools return aggregates only.

Change impact is evidence, never proof: it compares the devices a change targeted with the
devices it did not, before and after, and labels how specific and large the shift was."""

import re
from datetime import date, timedelta

import yaml

from pipeline import config, dq
from pipeline.business_metrics import ticket_sql
from pipeline.metrics import definitions, kpi_sql

CHANGE_METRICS = ("app_crash_rate", "bsod_rate", "boot_time_p50", "coverage", "tickets_per_1k", "it_downtime_per_100")
_ID = re.compile(r"^[A-Z]{2,4}-?\d+$|^GRP-[A-Z0-9-]+$|^SITE-[A-Z]+$")
LABEL_RANK = {"strong": 0, "moderate": 1, "weak": 2, "none": 3, "insufficient_data": 4}


def _safe(v: str) -> str:
    """Ids from the warehouse are inlined into cohort SQL only after this check."""
    if not _ID.match(v):
        raise ValueError(f"unexpected identifier {v!r}")
    return v


def cost_assumptions() -> dict:
    with open(config.COST_ASSUMPTIONS_PATH) as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- pure rules (unit-tested)

def evidence_label(rel_scope: float | None, rel_comp: float | None, scope_devices: int, events: int,
                   has_comparison: bool, rules: dict) -> str:
    """How strongly a metric shift in the targeted devices points at the change."""
    if rel_scope is None or scope_devices < rules["min_scope_devices"] or events < rules["min_events"]:
        return "insufficient_data"
    size = abs(rel_scope)
    if size < rules["moderate_rel"]:
        return "none"
    if not has_comparison:
        return "weak"                     # nothing to separate the change from a fleet-wide trend
    specific = rel_comp is None or abs(rel_scope) >= rules["specificity"] * abs(rel_comp)
    if not specific:
        return "weak"
    return "strong" if size >= rules["strong_rel"] else "moderate"


def remediation_verdict(rel: float | None, expected: str, rel_comp: float | None, has_comparison: bool,
                        rules: dict) -> str:
    """Did the metric move the intended way after the action, more than in the comparison group?"""
    if rel is None:
        return "not_yet_measurable"
    signed = rel if expected == "increase" else -rel
    if abs(rel) < rules["no_effect_rel"]:
        return "no_detectable_effect"
    if signed >= rules["effective_rel"]:
        if has_comparison and rel_comp is not None:
            comp_signed = rel_comp if expected == "increase" else -rel_comp
            if comp_signed >= 0.5 * signed:
                return "inconclusive"     # the comparison group improved as much
        return "effective"
    if signed <= -rules["effective_rel"]:
        return "worse"
    return "inconclusive"


# --------------------------------------------------------------------------- facts

def _facts(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE gold_ticket_fact AS
        SELECT number, iso_week, opened_date, opened_at, resolved_at, plant_code, device_class, device_key,
               category, priority, is_child, is_major, problem_id, change_id, mtta_min, mttr_min,
               sla_breached, reopen_count, state
        FROM silver_ticket""")
    con.execute("""
        CREATE OR REPLACE TABLE gold_downtime_fact AS
        SELECT event_id, iso_week, stop_date, start_at, plant_code, line_id, station, device_key, line_critical,
               minutes, reason_code, is_it, it_type, it_reclassified
        FROM silver_downtime""")
    sql, params = ticket_sql(["iso_week", "plant_code", "category"])
    con.execute(f"CREATE OR REPLACE TABLE gold_ticket_weekly AS {sql}", params)


# --------------------------------------------------------------------------- cost

def _cost(con) -> None:
    a = cost_assumptions()
    p, w, s = a["production"], a["workforce"], a["support"]
    margin = lambda b: "CASE plant_code " + " ".join(
        f"WHEN '{k}' THEN {v[b]}" for k, v in p["margin_per_line_minute"].items()) + " END"
    rate = lambda b: "CASE device_class " + " ".join(
        f"WHEN '{k}' THEN {v[b]}" for k, v in w["loaded_hourly_rate"].items()) + " END"
    ticket = lambda b: "CASE priority " + " ".join(
        f"WHEN {k} THEN {v[b]}" for k, v in s["cost_per_ticket"].items()) + " END"
    rec_lo, rec_hi = p["output_recovered"]["low_cost"], p["output_recovered"]["high_cost"]
    crash, bsod = w["minutes_lost_per_app_crash"], w["minutes_lost_per_blue_screen"]
    child = s["child_ticket_cost_share"]
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_cost_weekly AS
        SELECT iso_week, plant_code, 'production_downtime' AS cost_type,
               CASE it_type WHEN 'IT-APP' THEN 'MES client faults (IT-APP)'
                            ELSE 'Station PC faults (IT-TERM)' END AS driver,
               sum(minutes) AS quantity, 'line-stop minutes' AS quantity_unit,
               sum(minutes * {margin('low')} * (1 - {rec_lo})) AS cost_low,
               sum(minutes * {margin('high')} * (1 - {rec_hi})) AS cost_high
        FROM silver_downtime WHERE is_it GROUP BY ALL
        UNION ALL
        SELECT e.iso_week, d.plant_code, 'workforce_productivity',
               CASE e.event_type WHEN 'app_crash' THEN 'App crashes (office and engineering)'
                                 ELSE 'Blue screens (office and engineering)' END,
               count(*), 'events',
               sum(CASE e.event_type WHEN 'app_crash' THEN {crash['low']} ELSE {bsod['low']} END / 60.0 * {rate('low')}),
               sum(CASE e.event_type WHEN 'app_crash' THEN {crash['high']} ELSE {bsod['high']} END / 60.0 * {rate('high')})
        FROM silver_telemetry_event e JOIN silver_device d USING (device_key)
        WHERE d.device_class IN ('OF', 'EN') AND e.event_type IN ('app_crash', 'bugcheck') GROUP BY ALL
        UNION ALL
        SELECT iso_week, plant_code, 'it_support', 'Incidents: ' || category,
               count(*), 'tickets',
               sum({ticket('low')} * CASE WHEN is_child THEN {child} ELSE 1 END),
               sum({ticket('high')} * CASE WHEN is_child THEN {child} ELSE 1 END)
        FROM silver_ticket WHERE plant_code IS NOT NULL GROUP BY ALL""")
    dq.log_count(con, "cost_illustrative", "gold_cost_weekly", "true",
                 "costs use illustrative assumptions in metrics/cost_assumptions.yaml", "info")


# --------------------------------------------------------------------------- change impact

def _weeks(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT iso_week FROM gold_week ORDER BY 1").fetchall()]


def _cohort(scope_sql: str, inside: bool) -> str:
    return scope_sql if inside else (
        f"SELECT device_key FROM silver_device WHERE device_key NOT IN ({scope_sql})")


def _measure(con, metric: str, cohort_sql: str, w_from: str, w_to: str, app: str | None = None):
    """(value, devices, events) for a cohort over a week range."""
    if metric == "tickets_per_1k":
        r = con.execute(f"""
            SELECT 1000.0 * count(t.number) / nullif((SELECT count(*) FROM gold_device_week
                       WHERE intune_active AND iso_week BETWEEN ? AND ? AND device_key IN ({cohort_sql})), 0),
                   (SELECT count(*) FROM ({cohort_sql})), count(t.number)
            FROM gold_ticket_fact t WHERE t.iso_week BETWEEN ? AND ? AND t.device_key IN ({cohort_sql})""",
                        [w_from, w_to, w_from, w_to]).fetchone()
        return r
    if metric == "it_downtime_per_100":
        r = con.execute(f"""
            WITH term AS (SELECT device_key FROM silver_line_terminal WHERE device_key IN ({cohort_sql})),
                 n AS (SELECT count(*) AS terminals FROM term),
                 wk AS (SELECT count(*) AS weeks FROM gold_week WHERE iso_week BETWEEN ? AND ?)
            SELECT 100.0 * coalesce(sum(f.minutes), 0) / nullif(n.terminals, 0) / wk.weeks, n.terminals, count(f.event_id)
            FROM n, wk LEFT JOIN gold_downtime_fact f ON f.is_it AND f.iso_week BETWEEN ? AND ?
                 AND f.device_key IN (SELECT device_key FROM term)
            GROUP BY n.terminals, wk.weeks""", [w_from, w_to, w_from, w_to]).fetchone()
        return r
    if app:
        r = con.execute(f"""
            WITH dw AS (SELECT device_key, iso_week FROM gold_device_week
                        WHERE reporting AND iso_week BETWEEN ? AND ? AND device_key IN ({cohort_sql}))
            SELECT 1000.0 * (SELECT count(*) FROM gold_event_fact e JOIN dw USING (device_key, iso_week)
                             WHERE e.event_type = 'app_crash' AND e.app_name = ?) / nullif(count(*), 0),
                   count(DISTINCT device_key),
                   (SELECT count(*) FROM gold_event_fact e JOIN dw USING (device_key, iso_week)
                    WHERE e.event_type = 'app_crash' AND e.app_name = ?)
            FROM dw""", [w_from, w_to, app, app]).fetchone()
        return r
    sql, params = kpi_sql([], {}, w_from, w_to, cohort_sql)
    row = con.execute(sql, params).fetchone()
    cols = [d[0] for d in con.description]
    rec = dict(zip(cols, row))
    events = {"app_crash_rate": "app_crashes", "bsod_rate": "bugchecks", "boot_time_p50": "boot_samples",
              "coverage": "intune_active_device_weeks"}[metric]
    devices = rec["devices_active"] if metric == "coverage" else rec["devices_reporting"]
    return rec[metric], devices, rec[events]


def _rel(pre, post):
    if pre is None or post is None or pre == 0:
        return None
    return (post - pre) / pre


IMPACT_COLS = ("change_id", "description", "category", "actual_start", "change_week", "metric", "relevant",
               "pre_window", "post_window", "scope_pre", "scope_post", "comparison_pre", "comparison_post",
               "scope_devices", "rel_change_scope", "rel_change_comparison", "diff_in_diff", "events",
               "has_comparison", "direction", "evidence", "mechanism", "mechanism_change", "competing_changes",
               "note", "same_week_changes")


def relevant_metrics(category: str, description: str, rules: dict) -> list[str]:
    desc = (description or "").lower()
    for word, metrics in rules["keyword_metrics"].items():
        if word in desc:
            return metrics
    by_cat = rules["metrics_by_category"]
    return by_cat.get(category, by_cat["default"])


def _coverage_days(con, cohort_sql: str, d_from: date, d_to: date):
    """(coverage, devices, active device-days) on a day window: device-days with any telemetry /
    device-days enrolled and checked in."""
    return con.execute(f"""
        WITH days AS (SELECT d::DATE AS day FROM range(?::DATE, ?::DATE + INTERVAL 1 DAY, INTERVAL 1 DAY) t(d)),
        active AS (SELECT day, device_key FROM days JOIN silver_device s
                   ON s.enrolled_at::DATE <= day AND s.last_checkin_at::DATE >= day
                   WHERE s.device_key IN ({cohort_sql})),
        rep AS (SELECT DISTINCT event_date AS day, device_key FROM silver_telemetry_event
                WHERE event_date BETWEEN ? AND ?)
        SELECT count(r.device_key) / nullif(count(*), 0), count(DISTINCT a.device_key), count(*)
        FROM active a LEFT JOIN rep r USING (day, device_key)""", [d_from, d_to, d_from, d_to]).fetchone()


def _mechanism(con, metric: str, scope_sql: str, pre: tuple, post: tuple, increase: bool):
    """The app version or bug check code that carries most of the shift in scope."""
    if metric == "app_crash_rate":
        item = "app_name || ' ' || coalesce(app_version, '')"
        kind = "app_crash"
    elif metric == "bsod_rate":
        item, kind = "bugcheck_code", "bugcheck"
    else:
        return None
    row = con.execute(f"""
        SELECT {item} AS it,
               count(*) FILTER (WHERE iso_week BETWEEN ? AND ?) AS b,
               count(*) FILTER (WHERE iso_week BETWEEN ? AND ?) AS a
        FROM gold_event_fact WHERE event_type = ? AND device_key IN ({scope_sql})
        GROUP BY 1 ORDER BY (a - b) * {1 if increase else -1} DESC LIMIT 1""",
                      [pre[0], pre[1], post[0], post[1], kind]).fetchone()
    if not row or row[0] is None:
        return None
    return row[0].strip(), row[1], row[2]


def _change_impact(con) -> None:
    rules = definitions()["business"]["change_impact"]
    weeks = _weeks(con)
    fleet = con.execute("SELECT count(*) FROM silver_device WHERE lifecycle = 'active'").fetchone()[0]
    changes = con.execute("""
        SELECT change_id, description, category, actual_start, strftime(actual_start, '%G-W%V'), actual_end
        FROM silver_change WHERE implemented ORDER BY actual_start""").fetchall()
    descriptions = {c[0]: (c[1] or "").lower() for c in changes}
    rows = []
    for cid, desc, cat, start, cweek, end in changes:
        if cweek not in weeks:
            continue
        i = weeks.index(cweek)
        pre, post = weeks[max(0, i - rules["pre_weeks"]):i], weeks[i:i + rules["post_weeks"]]
        scope_sql = f"SELECT device_key FROM silver_change_scope WHERE change_id = '{_safe(cid)}'"
        comp_sql = _cohort(scope_sql, False)
        n_scope = con.execute(f"SELECT count(*) FROM ({scope_sql})").fetchone()[0]
        has_comp = n_scope < 0.95 * fleet
        relevant = relevant_metrics(cat, desc, rules)
        overlaps = ", ".join(c[0] for c in changes if c[4] == cweek and c[0] != cid)
        for metric in CHANGE_METRICS:
            r = dict.fromkeys(IMPACT_COLS)
            r.update(change_id=cid, description=desc, category=cat, actual_start=start, change_week=cweek,
                     metric=metric, relevant=metric in relevant, scope_devices=n_scope, has_comparison=has_comp,
                     events=0, same_week_changes=overlaps,
                     note="" if has_comp else "targets (almost) every device: no comparison group")
            if metric == "coverage":
                # Day windows: the change day onwards (next day if it landed in the afternoon).
                d0 = start.date() if start.hour < 12 else start.date() + timedelta(days=1)
                b_from = max(config.WINDOW_START, d0 - timedelta(days=rules["coverage_days_before"]))
                # A rollout that runs for weeks is judged until it finishes, not only on its first days.
                last = max(d0 + timedelta(days=rules["coverage_days_after"] - 1), end.date() if end else d0)
                a_to = min(config.WINDOW_END, last)
                if d0 <= config.WINDOW_START or d0 > config.WINDOW_END:
                    r.update(evidence="insufficient_data", note="change outside the data window")
                    rows.append(r)
                    continue
                s_pre = _coverage_days(con, scope_sql, b_from, d0 - timedelta(days=1))
                s_post = _coverage_days(con, scope_sql, d0, a_to)
                c_pre = _coverage_days(con, comp_sql, b_from, d0 - timedelta(days=1)) if has_comp else (None,) * 3
                c_post = _coverage_days(con, comp_sql, d0, a_to) if has_comp else (None,) * 3
                windows = (f"{b_from}..{d0 - timedelta(days=1)}", f"{d0}..{a_to}")
                pre_w = post_w = None
            else:
                if not pre:
                    r.update(evidence="insufficient_data", note="no weeks before the change in the data window")
                    rows.append(r)
                    continue
                pre_w, post_w = (pre[0], pre[-1]), (post[0], post[-1])
                s_pre, s_post = _measure(con, metric, scope_sql, *pre_w), _measure(con, metric, scope_sql, *post_w)
                c_pre = _measure(con, metric, comp_sql, *pre_w) if has_comp else (None,) * 3
                c_post = _measure(con, metric, comp_sql, *post_w) if has_comp else (None,) * 3
                windows = (f"{pre[0]}..{pre[-1]}", f"{post[0]}..{post[-1]}")
            rel_s, rel_c = _rel(s_pre[0], s_post[0]), _rel(c_pre[0], c_post[0])
            events = (s_pre[2] or 0) + (s_post[2] or 0)
            r.update(pre_window=windows[0], post_window=windows[1], scope_pre=s_pre[0], scope_post=s_post[0],
                     comparison_pre=c_pre[0], comparison_post=c_post[0], rel_change_scope=rel_s,
                     rel_change_comparison=rel_c, events=events,
                     direction=None if rel_s is None else ("increase" if rel_s > 0 else "decrease"),
                     diff_in_diff=(s_post[0] - s_pre[0]) - (c_post[0] - c_pre[0])
                     if None not in (s_pre[0], s_post[0], c_pre[0], c_post[0]) else None,
                     evidence=evidence_label(rel_s, rel_c, s_pre[1] or 0, events, has_comp,
                                             rules | rules.get("metric_thresholds", {}).get(metric, {})))
            if pre_w and r["evidence"] in ("strong", "moderate", "weak"):
                mech = _mechanism(con, metric, scope_sql, pre_w, post_w, rel_s > 0)
                if mech:
                    item, b, a = mech
                    r["mechanism"] = f"{'increase' if rel_s > 0 else 'decrease'} concentrated in {item} ({b} -> {a} events)"
                    name = item.split()[0].lower().removesuffix(".exe")
                    version = item.split()[1] if len(item.split()) > 1 else ""
                    hits = [c for c, d in descriptions.items() if name and name in d
                            and (not version or version in d or rel_s < 0)]
                    r["mechanism_change"] = ", ".join(hits) or None
                    if hits and cid not in hits:
                        # The shift is carried by something another change deployed.
                        if r["evidence"] in ("strong", "moderate"):
                            r["evidence"] = "weak"
                        r["note"] = (r["note"] + "; " if r["note"] else "") + \
                            f"shift explained by {', '.join(hits)} (same item)"
            rows.append(r)

    # Competing explanations: another relevant change with a same-direction shift on the same
    # metric whose change week falls inside this change's after-window (or the reverse).
    by_metric = {}
    for r in rows:
        if r["relevant"] and r["evidence"] in ("strong", "moderate", "weak"):
            by_metric.setdefault(r["metric"], []).append(r)
    for metric, group in by_metric.items():
        for r in group:
            lo, hi = (r["post_window"] or "..").split("..") if metric != "coverage" else (r["change_week"],) * 2
            rivals = [o["change_id"] for o in group if o is not r and o["direction"] == r["direction"]
                      and (lo <= o["change_week"] <= hi or o["change_week"] == r["change_week"])]
            r["competing_changes"] = ", ".join(rivals) or None

    con.execute("CREATE OR REPLACE TABLE gold_change_impact (" + ", ".join(
        f"{c} {'TIMESTAMP' if c == 'actual_start' else 'BOOLEAN' if c in ('relevant', 'has_comparison') else 'BIGINT' if c in ('scope_devices', 'events') else 'DOUBLE' if c in ('scope_pre', 'scope_post', 'comparison_pre', 'comparison_post', 'rel_change_scope', 'rel_change_comparison', 'diff_in_diff') else 'VARCHAR'}"
        for c in IMPACT_COLS) + ")")
    con.executemany(f"INSERT INTO gold_change_impact VALUES ({','.join('?' * len(IMPACT_COLS))})",
                    [[r[c] for c in IMPACT_COLS] for r in rows])
    # One row per change: its strongest relevant metric (whole row, so nothing is mixed across metrics).
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_change_summary AS
        SELECT change_id, description, category, actual_start, change_week, scope_devices, has_comparison,
               metric AS top_metric, evidence, direction, rel_change_scope AS top_rel_change, pre_window,
               post_window, mechanism, mechanism_change, competing_changes, same_week_changes
        FROM gold_change_impact WHERE relevant
        QUALIFY row_number() OVER (PARTITION BY change_id
                 ORDER BY {_rank_sql()}, mechanism_change IS NULL, abs(coalesce(rel_change_scope, 0)) DESC) = 1""")
    dq.log_count(con, "change_impact_strong", "gold_change_summary", "evidence = 'strong'",
                 "change followed by a large, scope-specific shift in a relevant metric (evidence, not proof)")


def _rank_sql() -> str:
    return "CASE evidence " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in LABEL_RANK.items()) + " END"


# --------------------------------------------------------------------------- remediation

def _effect_weeks(weeks: list[str], actioned: date) -> tuple[list[str], list[str]]:
    """Fixes on Mon/Tue count from that week; later in the week, from the next week.
    Before = the week before the effect; after = the effect week and the next."""
    week = f"{actioned.isocalendar()[0]}-W{actioned.isocalendar()[1]:02d}"
    if week not in weeks:
        return [], []
    i = weeks.index(week) + (0 if actioned.weekday() <= 1 else 1)
    return weeks[max(0, i - 1):i], weeks[i:i + 2]


def _remediation(con) -> None:
    rules = definitions()["business"]["remediation"]
    with open(config.ROOT / "metrics" / "remediation_measures.yaml") as f:
        measures = yaml.safe_load(f)
    weeks = _weeks(con)
    fleet = con.execute("SELECT count(*) FROM silver_device WHERE lifecycle = 'active'").fetchone()[0]
    out = []
    for aid, prb, title, cid, status, actioned, verified in con.execute("""
            SELECT action_id, problem_id, title, change_id, status, actioned_on, verified
            FROM silver_remediation ORDER BY action_id""").fetchall():
        m = measures.get(aid, {"measure": "none", "reason": "no measure defined"})
        rec = {"action_id": aid, "problem_id": prb, "title": title, "change_id": cid, "status": status,
               "actioned_on": actioned, "verified": verified, "metric": m.get("metric"), "app": m.get("app"),
               "scope_group": m.get("scope_group"), "expected": m.get("expected"), "before_weeks": None,
               "after_weeks": None, "before": None, "after": None, "rel_change": None, "comparison_before": None,
               "comparison_after": None, "rel_change_comparison": None, "implementation_evidence": None,
               "verdict": None, "explanation": None}
        change = con.execute("SELECT implemented, rolled_back, state FROM silver_change WHERE change_id = ?",
                             [cid]).fetchone() if cid else None
        if status in ("Open", "In progress"):
            rec["verdict"], rec["explanation"] = "open", "Action not completed yet."
        elif status == "Done" and change and not change[0]:
            rec["verdict"] = "claimed_not_implemented"
            rec["explanation"] = f"Linked change {cid} is {change[2].lower()}{' (rolled back)' if change[1] else ''}."
        elif m.get("measure") == "none":
            rec["verdict"], rec["explanation"] = "not_measurable_process", m.get("reason")
        elif status != "Done" or actioned is None or actioned > config.WINDOW_END - timedelta(days=6):
            rec["verdict"] = "not_yet_measurable"
            rec["explanation"] = "Not actioned within the data window; measure after it lands."
        else:
            scope_sql = (f"SELECT device_key FROM silver_group_member "
                         f"WHERE group_name = '{_safe(m['scope_group'])}'")
            check = m.get("implementation_check")
            failed = None
            if check == "no_target_release_after":
                n = con.execute(f"""SELECT count(*) FROM gold_rollout_reconciliation
                                    WHERE first_target_seen > ? AND device_key IN ({scope_sql})""",
                                [actioned]).fetchone()[0]
                rec["implementation_evidence"] = f"{n} devices in scope first reported Win11 25H2 after {actioned}"
                failed = n > 0
            elif check == "app_version_observed":
                n = con.execute(f"""SELECT count(DISTINCT device_key) FROM gold_event_fact
                                    WHERE app_name = ? AND app_version = ? AND event_date >= ?
                                      AND device_key IN ({scope_sql})""",
                                [m["app"], m["version"], actioned]).fetchone()[0]
                rec["implementation_evidence"] = f"{m['app']} {m['version']} seen on {n} devices after {actioned}"
                failed = n == 0
            if failed:
                rec["verdict"] = "claimed_not_implemented"
                rec["explanation"] = "Marked done, but telemetry shows it did not take effect in scope."
            else:
                before, after = _effect_weeks(weeks, actioned)
                if not before or not after:
                    rec["verdict"], rec["explanation"] = "not_yet_measurable", "Too little data after the action."
                else:
                    has_comp = con.execute(f"SELECT count(*) FROM ({scope_sql})").fetchone()[0] < 0.95 * fleet
                    b = _measure(con, m["metric"], scope_sql, before[0], before[-1], m.get("app"))
                    a = _measure(con, m["metric"], scope_sql, after[0], after[-1], m.get("app"))
                    cb = ca = (None, None, None)
                    if has_comp:
                        cb = _measure(con, m["metric"], _cohort(scope_sql, False), before[0], before[-1], m.get("app"))
                        ca = _measure(con, m["metric"], _cohort(scope_sql, False), after[0], after[-1], m.get("app"))
                    rel, rel_c = _rel(b[0], a[0]), _rel(cb[0], ca[0])
                    rec.update(before_weeks=f"{before[0]}..{before[-1]}", after_weeks=f"{after[0]}..{after[-1]}",
                               before=b[0], after=a[0], rel_change=rel, comparison_before=cb[0],
                               comparison_after=ca[0], rel_change_comparison=rel_c)
                    rec["verdict"] = remediation_verdict(rel, m["expected"], rel_c, has_comp, rules)
                    rec["explanation"] = {
                        "effective": "The metric moved the intended way, beyond the comparison group.",
                        "no_detectable_effect": "No meaningful change in the metric after the action.",
                        "inconclusive": "The change is small or matched by the comparison group.",
                        "worse": "The metric moved the wrong way after the action.",
                        "not_yet_measurable": "Not enough data.",
                    }[rec["verdict"]]
        out.append(rec)
    cols = list(out[0])
    con.execute("CREATE OR REPLACE TABLE gold_remediation_effect (" + ", ".join(
        f"{c} {'DOUBLE' if c in ('before', 'after', 'rel_change', 'comparison_before', 'comparison_after', 'rel_change_comparison') else 'DATE' if c == 'actioned_on' else 'BOOLEAN' if c == 'verified' else 'VARCHAR'}"
        for c in cols) + ")")
    for r in out:
        con.execute(f"INSERT INTO gold_remediation_effect VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
    dq.log_count(con, "remediation_claimed_not_implemented", "gold_remediation_effect",
                 "verdict = 'claimed_not_implemented'", "register says done; evidence says otherwise", "warning")


# --------------------------------------------------------------------------- timeline

def _timeline(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE gold_timeline AS
        SELECT c.actual_start AS event_at, 'change' AS kind, c.change_id AS ref,
               c.description AS title,
               CASE WHEN c.target_group = 'SITE-DBN' THEN 'DBN' ELSE 'ALL' END AS plant_code,
               c.change_type || ' change, ' || coalesce(s.n, 0) || ' devices in scope'
                 || CASE WHEN c.rolled_back THEN ', rolled back' ELSE '' END AS detail
        FROM silver_change c
        LEFT JOIN (SELECT change_id, count(*) AS n FROM silver_change_scope GROUP BY 1) s USING (change_id)
        WHERE c.actual_start IS NOT NULL
        UNION ALL
        SELECT t.opened_at, 'major_incident', t.number, 'Major incident: ' || t.category,
               coalesce(t.plant_code, 'ALL'),
               'P' || t.priority || ', ' || (SELECT count(*) FROM silver_ticket c WHERE c.parent_incident = t.number)
                 || ' linked tickets, resolved ' || coalesce(strftime(t.resolved_at, '%a %d %b %H:%M'), 'not yet')
        FROM silver_ticket t WHERE t.is_major
        UNION ALL
        SELECT t.opened_at, 'detection', t.number, 'Monitoring ticket raised (' || t.category || ')',
               coalesce(t.plant_code, 'ALL'),
               'P' || t.priority || ', ' || coalesce('resolved ' || strftime(t.resolved_at, '%a %d %b %H:%M'), 'open')
        FROM silver_ticket t WHERE t.category = 'Monitoring' AND t.device_key IS NULL
        UNION ALL
        SELECT min(t.opened_at), 'problem', t.problem_id, 'Problem record first linked to an incident',
               'ALL', count(*) || ' incidents linked'
        FROM silver_ticket t WHERE t.problem_id IS NOT NULL GROUP BY t.problem_id
        UNION ALL
        SELECT min(day)::TIMESTAMP, 'telemetry_outage', 'OUTAGE-' || plant_code || '-' || iso_week,
               'Telemetry outage', plant_code,
               count(*) || ' weekday(s) with almost no telemetry, ' || strftime(min(day), '%a %d %b')
                 || ' to ' || strftime(max(day), '%a %d %b')
        FROM gold_coverage_daily
        WHERE is_weekday AND device_class = 'ALL' AND plant_code <> 'ALL' AND reporting_vs_weekday_median < 0.5
        GROUP BY plant_code, iso_week
        UNION ALL
        SELECT r.actioned_on::TIMESTAMP, 'remediation', r.action_id, r.title, 'ALL',
               'Status ' || r.status || ', verdict ' || r.verdict
        FROM gold_remediation_effect r WHERE r.actioned_on IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TABLE gold_timeline AS
                   SELECT *, strftime(event_at, '%G-W%V') AS iso_week FROM gold_timeline ORDER BY event_at""")


def build(con) -> None:
    _facts(con)
    _cost(con)
    _change_impact(con)
    _remediation(con)
    _timeline(con)
