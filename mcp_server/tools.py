"""Governed fleet tools. Plain Python so the MCP server, the weekly briefing and
the evals all call exactly the same code.

Every response is an envelope with the metric definition, the period, the
filters, device counts per row, and the coverage / data quality warnings that
apply to the slice. Groups under the minimum device count are suppressed, and
the whole response is checked for device identifiers before it is returned."""

import functools
import inspect
import time

import duckdb

from mcp_server import audit
from mcp_server.guardrails import (
    DIMENSIONS, IdentifierLeak, OutboundCheck, ToolInputError, Validator, min_devices,
)
from pipeline import config
from pipeline.metrics import definitions, kpi_sql

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

RECONCILIATION_MEANING = {
    "confirmed": "Tracker says Done and telemetry shows the device on Win11 25H2.",
    "claimed_not_observed": "Tracker says Done but the device's latest telemetry is still on an older build.",
    "unverifiable_stale_telemetry": "Tracker says Done but the device stopped reporting before its scheduled date.",
    "observed_not_claimed": "Telemetry shows 25H2 but the tracker does not say Done.",
    "rolled_back_observed": "Telemetry showed 25H2 and later an older build.",
    "consistent_not_upgraded": "Tracker does not say Done and telemetry agrees (not on 25H2).",
    "exempt_ok": "Win10 LTSC shop-floor device, exempt, still on LTSC.",
    "exempt_but_upgraded": "Exempt device that telemetry shows on 25H2.",
    "no_telemetry": "Device sent no telemetry in the window.",
    "not_in_intune": "Hostname in the tracker but not in Intune.",
}

# Columns that are counts of something about the group, shown with each row.
BASIS = {
    "startup_count": ["startups"],
    "boot_time_p50": ["boot_samples"], "boot_time_p90": ["boot_samples"],
    "logon_time_p50": ["logon_samples"], "logon_time_p90": ["logon_samples"],
    "app_crash_rate": ["app_crashes", "reporting_device_weeks"],
    "bsod_rate": ["bugchecks", "reporting_device_weeks"],
    "coverage": ["covered_device_weeks", "intune_active_device_weeks"],
}


def _r(x, nd=3):
    return round(x, nd) if isinstance(x, float) else x


def _public(col: str) -> str:
    return {v: k for k, v in DIMENSIONS.items()}.get(col, col)


def audited(fn):
    """Audit-log every call and run the outbound identifier check on the result."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        bound = sig.bind(self, *args, **kwargs)
        call_args = {k: v for k, v in bound.arguments.items() if k != "self"}
        started = time.perf_counter()
        try:
            result = fn(self, *args, **kwargs)
            self._out.check(result)
        except (ToolInputError, IdentifierLeak) as e:
            audit.write(self.caller, fn.__name__, call_args, started, ok=False, error=str(e))
            raise
        except Exception as e:
            audit.write(self.caller, fn.__name__, call_args, started, ok=False,
                        error=f"{type(e).__name__}: {e}")
            raise
        audit.write(self.caller, fn.__name__, call_args, started, ok=True,
                    rows=len(result.get("rows", [])), suppressed=result.get("suppressed_groups", 0),
                    warnings=len(result.get("warnings", [])))
        return result

    return wrapper


class FleetTools:
    def __init__(self, db_path=None, caller: str = "mcp"):
        self.con = duckdb.connect(str(db_path or config.DB_PATH), read_only=True)
        self.v = Validator(self.con)
        self._out = OutboundCheck(self.con)
        self.caller = caller
        self.defs = definitions()
        self.min_devices = min_devices()

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, params=None) -> list[dict]:
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _metric_def(self, name: str) -> dict:
        m = self.defs["metrics"][name]
        return {"name": name, "label": m["label"], "formula": m["expression"], "unit": m["unit"],
                "grain": m["grain"], "direction": m["direction"],
                "device_count_basis": m["device_count"], "caveats": m.get("caveats", [])}

    def _period(self, a: str, b: str) -> dict:
        start, end = self.v.week_dates[a][0], self.v.week_dates[b][1]
        label = a if a == b else f"{a} to {b}"
        return {"week_from": a, "week_to": b, "start_date": str(start), "end_date": str(end),
                "label": f"{label} (Mon {start:%d %b} to Sun {end:%d %b %Y}, SAST)"}

    def _warnings(self, a: str, b: str, filters: dict) -> list[dict]:
        where, params = ["iso_week BETWEEN ? AND ?"], [a, b]
        if "plant_code" in filters:
            where.append("plant_code IN (?, 'ALL')"); params.append(filters["plant_code"])
        if "device_class" in filters:
            where.append("device_class IN (?, 'ALL')"); params.append(filters["device_class"])
        else:
            # Class-level warnings only matter to a fleet or plant question when critical.
            where.append("(device_class = 'ALL' OR severity = 'critical')")
        rows = self._q(f"""SELECT iso_week, plant_code, device_class, code, severity, message
                           FROM gold_slice_warnings WHERE {' AND '.join(where)}""", params)
        out = [{"week": r["iso_week"], "scope": f"plant={r['plant_code']}, device_class={r['device_class']}",
                "code": r["code"], "severity": r["severity"], "message": r["message"]} for r in rows]
        return sorted(out, key=lambda w: (SEVERITY_ORDER[w["severity"]], w["week"], w["scope"]))

    def _status(self, warnings: list[dict]) -> dict:
        worst = min((SEVERITY_ORDER[w["severity"]] for w in warnings), default=3)
        status = {0: "critical", 1: "warning", 2: "ok_with_notes", 3: "ok"}[worst]
        return {"status": status,
                "critical": sum(w["severity"] == "critical" for w in warnings),
                "warning": sum(w["severity"] == "warning" for w in warnings)}

    def _shape(self, row: dict, metric: str, dims: list[str]) -> tuple[dict, bool]:
        """Public row for one group; suppressed if too few devices."""
        basis_col = self.defs["metrics"][metric]["device_count"]
        devices = row.get(basis_col) or 0
        out = {_public(d): row[d] for d in dims}
        if devices < self.min_devices:
            out |= {"value": None, "suppressed": True, basis_col: f"<{self.min_devices}"}
            return out, True
        out |= {"value": _r(row[metric]), "devices_reporting": row["devices_reporting"],
                "devices_active": row["devices_active"]}
        out |= {c: _r(row[c]) for c in BASIS[metric]}
        return out, False

    def _kpi_rows(self, metric, dims, filters, a, b, cohort_sql=None, cohort_params=()):
        sql, params = kpi_sql(dims, filters, a, b, cohort_sql)
        rows = self._q(sql + (f" ORDER BY {', '.join(dims)}" if dims else ""), params + list(cohort_params))
        shaped = [self._shape(r, metric, dims) for r in rows]
        return [s for s, _ in shaped], sum(sup for _, sup in shaped)

    def _envelope(self, *, metric=None, period=None, filters=None, rows=None, warnings=None,
                  notes=None, suppressed=0, **extra) -> dict:
        warnings = warnings or []
        env = {"metric_definition": self._metric_def(metric) if metric else None,
               "period": period,
               "filters": {_public(k): v for k, v in (filters or {}).items()} or "none (whole fleet)",
               "rows": rows or [],
               "suppressed_groups": suppressed,
               "suppression_rule": f"groups with fewer than {self.min_devices} devices are suppressed",
               "data_quality": self._status(warnings),
               "warnings": warnings,
               "notes": notes or []}
        env.update(extra)
        return env

    # ------------------------------------------------------------------ tools
    @audited
    def list_allowed_values(self) -> dict:
        return self._envelope(
            rows=[],
            metrics={n: {"label": m["label"], "unit": m["unit"]} for n, m in self.defs["metrics"].items()},
            dimensions={"plant": ["ROS (Rosslyn)", "GQB (Gqeberha)", "DBN (Durban)"],
                        "device_class": ["OF (office laptop)", "EN (engineering workstation)",
                                         "SF (shop-floor terminal)"],
                        "model": self.v.models,
                        "os_build": self.v.releases},
            weeks={w: f"{s} to {e}" for w, (s, e) in self.v.week_dates.items()},
            compare_group_by=["none", *DIMENSIONS, "rollout_cohort"],
            event_types=["app_crash", "bugcheck"],
            rollout_statuses=RECONCILIATION_MEANING,
        )

    @audited
    def get_metric_definition(self, metric: str) -> dict:
        m = self.v.metric(metric)
        return self._envelope(metric=m, grain_note=self.defs["grain_note"].strip(),
                              thresholds=self.defs["thresholds"].get(m))

    @audited
    def get_kpi(self, metric: str, plant: str | None = None, device_class: str | None = None,
                model: str | None = None, os_build: str | None = None,
                week_from: str | None = None, week_to: str | None = None,
                group_by: str | None = None) -> dict:
        m = self.v.metric(metric)
        f = self.v.filters(plant, device_class, model, os_build)
        a, b = self.v.week_range(week_from, week_to)
        gb = self.v.choice(group_by, list(DIMENSIONS), "group_by")
        dims = [DIMENSIONS[gb]] if gb else []
        weekly, s1 = self._kpi_rows(m, ["iso_week", *dims], f, a, b)
        total, s2 = self._kpi_rows(m, dims, f, a, b)
        notes = ["Rows are per ISO week; period_total aggregates the whole period."]
        if m.startswith("logon") and f.get("device_class") in (None, "SF"):
            notes.append("Shop-floor kiosks with auto-logon have no logon duration; logon metrics "
                         "cover only devices with interactive logon.")
        return self._envelope(metric=m, period=self._period(a, b), filters=f, rows=weekly,
                              period_total=total, suppressed=s1 + s2,
                              warnings=self._warnings(a, b, f), notes=notes)

    def _reporting_sql(self, a: str, b: str) -> str:
        # Weeks are validated against the warehouse's own list before reaching here.
        return (f"SELECT device_key FROM gold_device_week WHERE reporting "
                f"AND iso_week BETWEEN '{a}' AND '{b}'")

    @audited
    def compare_cohorts(self, metric: str, group_by: str, period_a: str, period_b: str,
                        like_for_like: bool = True, plant: str | None = None,
                        device_class: str | None = None, model: str | None = None,
                        os_build: str | None = None) -> dict:
        m = self.v.metric(metric)
        f = self.v.filters(plant, device_class, model, os_build)
        a1, a2 = self.v.period(period_a)
        b1, b2 = self.v.period(period_b)
        gb = self.v.choice(group_by, ["none", *DIMENSIONS, "rollout_cohort"], "group_by")
        if not isinstance(like_for_like, bool):
            raise ToolInputError("like_for_like must be true or false.")
        lfl_sql = f"{self._reporting_sql(a1, a2)} INTERSECT {self._reporting_sql(b1, b2)}"

        # Each group is a (label, cohort_sql, params) device set; dims grouping is done in SQL.
        if gb == "rollout_cohort":
            cohort_def = f"""
                SELECT device_key,
                       CASE WHEN ra = rb THEN 'stayed on ' || ra ELSE ra || ' -> ' || rb END AS cohort
                FROM (SELECT device_key,
                             arg_max(os_release, iso_week) FILTER (WHERE iso_week BETWEEN '{a1}' AND '{a2}') AS ra,
                             arg_max(os_release, iso_week) FILTER (WHERE iso_week BETWEEN '{b1}' AND '{b2}') AS rb
                      FROM gold_device_week GROUP BY 1)
                WHERE ra IS NOT NULL AND rb IS NOT NULL"""
            labels = [r["cohort"] for r in self._q(f"SELECT DISTINCT cohort FROM ({cohort_def}) ORDER BY 1")]
            groups = [(lab, f"SELECT device_key FROM ({cohort_def}) WHERE cohort = ?", [lab]) for lab in labels]
            dims = []
        else:
            groups = [(None, None, [])]
            dims = [] if gb == "none" else [DIMENSIONS[gb]]

        variants = {"all_reporting": None}
        if like_for_like:
            variants = {"like_for_like": lfl_sql, "all_reporting": None}

        rows, suppressed = [], 0
        for label, gsql, gparams in groups:
            per_variant = {}
            for vname, vsql in variants.items():
                parts = [p for p in (gsql, vsql) if p]
                cohort_sql = " INTERSECT ".join(f"({p})" for p in parts) if parts else None
                ra, sa = self._kpi_rows(m, dims, f, a1, a2, cohort_sql, gparams)
                rb, sb = self._kpi_rows(m, dims, f, b1, b2, cohort_sql, gparams)
                suppressed += sa + sb
                key = lambda r: tuple(r.get(_public(d)) for d in dims)
                bmap = {key(r): r for r in rb}
                for r in ra:
                    other = bmap.get(key(r), {})
                    va, vb = r.get("value"), other.get("value")
                    entry = per_variant.setdefault(key(r), {_public(d): r[_public(d)] for d in dims})
                    entry[vname] = {
                        "period_a": va, "period_b": vb,
                        "change": _r(vb - va) if va is not None and vb is not None else None,
                        "pct_change": _r((vb - va) / va) if va not in (None, 0) and vb is not None else None,
                        "devices_a": r.get("devices_reporting", r.get("devices_active")),
                        "devices_b": other.get("devices_reporting", other.get("devices_active")),
                    }
            for entry in per_variant.values():
                if label is not None:
                    entry = {"rollout_cohort": label, **entry}
                rows.append(entry)

        notes = []
        if like_for_like:
            fw = "".join(f" AND {col} = ?" for col in f)
            n = self._q(f"""SELECT count(DISTINCT device_key) AS n FROM gold_device_week
                            WHERE iso_week BETWEEN ? AND ?{fw} AND device_key IN ({lfl_sql})""",
                        [min(a1, b1), max(a2, b2), *f.values()])[0]["n"]
            notes.append(f"like_for_like restricts both periods to the {n} devices that reported in both "
                         "periods, so changes are not caused by devices joining or leaving the data. "
                         "all_reporting shows every reporting device for contrast.")
        if gb == "rollout_cohort":
            notes.append("To credit a change to the 25H2 upgrade, compare the upgraded cohort's change with "
                         "the change in a 'stayed on' cohort over the same weeks (difference-in-differences). "
                         "Cohorts are not randomised; plant, class and model mix differ between them.")
        warnings = self._warnings(min(a1, b1), max(a2, b2), f)
        return self._envelope(metric=m, period={"period_a": self._period(a1, a2), "period_b": self._period(b1, b2)},
                              filters=f, rows=rows, suppressed=suppressed, warnings=warnings, notes=notes,
                              group_by=gb, like_for_like=like_for_like)

    @audited
    def get_coverage(self, plant: str | None = None, device_class: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     granularity: str | None = None) -> dict:
        p, c = self.v.plant(plant), self.v.device_class(device_class)
        weeks = self.v.weeks
        d1 = self.v.day(date_from, self.v.week_dates[weeks[0]][0])
        d2 = self.v.day(date_to, self.v.week_dates[weeks[-1]][1])
        if d1 > d2:
            raise ToolInputError("date_from is after date_to.")
        gran = self.v.choice(granularity, ["week", "day"], "granularity", "week")
        plant_vals = [p] if p else ["ALL", "ROS", "GQB", "DBN"]
        cls = c or "ALL"
        a = min(w for w in weeks if self.v.week_dates[w][1] >= d1)
        b = max(w for w in weeks if self.v.week_dates[w][0] <= d2)
        ph = ", ".join("?" for _ in plant_vals)
        if gran == "day":
            raw = self._q(f"""
                SELECT day, iso_week, is_weekday, plant_code, intune_active_devices, reporting_devices,
                       coverage, reporting_vs_weekday_median
                FROM gold_coverage_daily
                WHERE day BETWEEN ? AND ? AND device_class = ? AND plant_code IN ({ph})
                ORDER BY day, plant_code""", [d1, d2, cls, *plant_vals])
            size_col = "intune_active_devices"
        else:
            raw = self._q(f"""
                SELECT iso_week, plant_code, intune_active_device_weeks AS intune_active_devices,
                       covered_device_weeks AS reporting_devices, coverage
                FROM gold_kpi_weekly
                WHERE iso_week BETWEEN ? AND ? AND device_class = ? AND model = 'ALL' AND os_release = 'ALL'
                  AND plant_code IN ({ph})
                ORDER BY iso_week, plant_code""", [a, b, cls, *plant_vals])
            size_col = "intune_active_devices"
        rows, sup = [], 0
        for r in raw:
            r = {_public(k): (str(v) if k == "day" else _r(v)) for k, v in r.items()}
            if r[size_col] < self.min_devices:
                r = {k: v for k, v in r.items() if k in ("day", "iso_week", "plant")} | {"suppressed": True}
                sup += 1
            rows.append(r)
        f = {k: v for k, v in (("plant_code", p), ("device_class", c)) if v}
        notes = ["Weekly coverage = devices that were Intune-active and sent any telemetry that week / "
                 "Intune-active devices."]
        if gran == "day":
            notes.append("Not every device boots every day, so daily coverage is naturally lower than weekly. "
                         "reporting_vs_weekday_median compares the day with a normal weekday for the same slice; "
                         "weekday values far below 1.0 indicate an outage.")
        return self._envelope(metric="coverage", period=self._period(a, b) | {"date_from": str(d1), "date_to": str(d2)},
                              filters=f, rows=rows, suppressed=sup, warnings=self._warnings(a, b, f),
                              notes=notes, granularity=gran)

    @audited
    def get_data_quality_status(self, week: str | None = None) -> dict:
        w = self.v.week(week)
        checks = self._q("""SELECT check_name, table_name, rows_affected, action_taken, severity
                            FROM dq_log WHERE rows_affected > 0
                            ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, check_name""")
        quarantine = self._q("""SELECT source, reason, retained, count(*) AS rows FROM quarantine
                                GROUP BY ALL ORDER BY source, rows DESC""")
        layers = self._q("""
            SELECT 'bronze_telemetry_lines' AS table_name, count(*) AS rows FROM bronze_telemetry_lines UNION ALL
            SELECT 'silver_telemetry_event', count(*) FROM silver_telemetry_event UNION ALL
            SELECT 'bronze_intune_devices', count(*) FROM bronze_intune_devices UNION ALL
            SELECT 'silver_device', count(*) FROM silver_device UNION ALL
            SELECT 'bronze_tracker', count(*) FROM bronze_tracker UNION ALL
            SELECT 'silver_rollout_tracker', count(*) FROM silver_rollout_tracker""")
        built = self._q("SELECT max(run_at) AS t FROM dq_log")[0]["t"]
        if w:
            warnings, period = self._warnings(w, w, {}), self._period(w, w)
        else:
            a, b = self.v.weeks[0], self.v.weeks[-1]
            warnings, period = self._warnings(a, b, {}), self._period(a, b)
        return self._envelope(period=period, rows=checks, warnings=warnings,
                              pipeline_built_at=str(built), layer_row_counts=layers,
                              quarantine_summary=quarantine,
                              notes=["Quarantined rows are held in the warehouse for review; only counts are "
                                     "shown here. 'retained' rows stay in Silver with the bad field nulled."])

    @audited
    def list_threshold_breaches(self, week: str) -> dict:
        w = self.v.week(week)
        if w is None:
            raise ToolInputError("week is required, e.g. 2026-W41.")
        raw = self._q("""SELECT * FROM gold_threshold_breaches WHERE iso_week = ?
                         ORDER BY direction = 'better', abs(change) DESC""", [w])
        week_warnings = self._warnings(w, w, {})
        rows = []
        for r in raw:
            md = self.defs["metrics"][r["metric"]]
            dim, val = r["slice_dimension"], r["slice_value"]
            related = [x for x in week_warnings if x["severity"] != "info" and (
                dim == "fleet" or
                (dim == "plant_code" and x["scope"].startswith(f"plant={val},")) or
                (dim == "device_class" and x["scope"].endswith(f"device_class={val}")) or
                x["scope"] == "plant=ALL, device_class=ALL")]
            rows.append({
                "slice": f"{_public(dim)}={val}" if dim != "fleet" else "whole fleet",
                "metric": r["metric"], "metric_label": md["label"], "unit": md["unit"],
                "previous_week": r["previous_week"], "previous_value": _r(r["previous_value"]),
                "value": _r(r["value"]), "change": _r(r["change"]),
                "change_type": "relative" if r["change_type"] == "pct" else "absolute",
                "direction": r["direction"], "devices_reporting": r["devices_reporting"],
                "events_or_startups": r["events"],
                "confounding_warnings": [x["code"] for x in related],
            })
        return self._envelope(period=self._period(w, w), rows=rows, warnings=week_warnings,
                              thresholds=self.defs["thresholds"],
                              notes=["A breach is a week-over-week change beyond the threshold in "
                                     "metrics/definitions.yaml. It is a signal to investigate, not a root cause.",
                                     "Breaches listing confounding_warnings coincide with coverage or cohort "
                                     "changes; check like-for-like before reading them as real."])

    @audited
    def get_rollout_status(self, plant: str | None = None, wave: str | None = None) -> dict:
        p = self.v.plant(plant)
        waves = [r[0] for r in self.con.execute(
            "SELECT DISTINCT tracker_wave FROM gold_rollout_summary ORDER BY 1").fetchall()]
        wv = self.v.choice(wave, waves, "wave")
        where, params = ["true"], []
        if p:
            where.append("plant_code = ?"); params.append(p)
        if wv:
            where.append("tracker_wave = ?"); params.append(wv)
        raw = self._q(f"""SELECT tracker_wave, tracker_status, reconciliation_status, sum(devices)::BIGINT AS devices
                          FROM gold_rollout_summary WHERE {' AND '.join(where)}
                          GROUP BY ALL ORDER BY tracker_wave, tracker_status, devices DESC""", params)
        rows, sup = [], 0
        for r in raw:
            if r["devices"] < self.min_devices:
                r = r | {"devices": f"<{self.min_devices}", "suppressed": True}
                sup += 1
            rows.append(r)
        totals = self._q(f"""SELECT reconciliation_status, sum(devices)::BIGINT AS devices
                             FROM gold_rollout_summary WHERE {' AND '.join(where)}
                             GROUP BY 1 ORDER BY 2 DESC""", params)
        mix_f = {"plant_code": p} if p else {}
        mix = self._q(f"""SELECT iso_week, os_release, devices_reporting FROM gold_kpi_weekly
                          WHERE plant_code = ? AND device_class = 'ALL' AND model = 'ALL' AND os_release <> 'ALL'
                          ORDER BY iso_week, os_release""", [p or "ALL"])
        mix = [m if m["devices_reporting"] >= self.min_devices else m | {"devices_reporting": f"<{self.min_devices}"}
               for m in mix]
        a, b = self.v.weeks[0], self.v.weeks[-1]
        f = {k: v for k, v in (("plant_code", p), ("wave", wv)) if v}
        return self._envelope(period=self._period(a, b), filters=f, rows=rows, suppressed=sup,
                              totals_by_reconciliation_status=totals,
                              status_meanings=RECONCILIATION_MEANING,
                              weekly_release_mix_reporting_devices=mix,
                              warnings=self._warnings(a, b, mix_f),
                              notes=["The rollout tracker is a manual claim; reconciliation_status compares it "
                                     "with what device telemetry reports (latest build seen in the window)."])

    @audited
    def get_top_events(self, event_type: str, week_from: str | None = None, week_to: str | None = None,
                       plant: str | None = None, device_class: str | None = None, model: str | None = None,
                       os_build: str | None = None, limit: int | None = None) -> dict:
        et = self.v.choice(event_type, ["app_crash", "bugcheck"], "event_type")
        f = self.v.filters(plant, device_class, model, os_build)
        a, b = self.v.week_range(week_from, week_to)
        n = self.v.limit(limit)
        item = "app_name, app_version" if et == "app_crash" else "bugcheck_code"
        where, params = ["iso_week BETWEEN ? AND ?"], [a, b]
        for col, val in f.items():
            where.append(f"{col} = ?"); params.append(val)
        raw = self._q(f"""
            WITH dw AS (SELECT device_key, iso_week FROM gold_device_week WHERE {' AND '.join(where)})
            SELECT {item}, count(*) AS events, count(DISTINCT e.device_key) AS devices,
                   histogram(e.iso_week) AS events_by_week
            FROM gold_event_fact e JOIN dw USING (device_key, iso_week)
            WHERE e.event_type = ?
            GROUP BY {item} ORDER BY events DESC LIMIT ?""", params + [et, n])
        rows, sup = [], 0
        for r in raw:
            hist = r["events_by_week"]
            if set(hist) == {"key", "value"}:          # older DuckDB MAP representation
                hist = dict(zip(hist["key"], hist["value"]))
            r["events_by_week"] = dict(sorted(hist.items()))
            if r["devices"] < self.min_devices:
                r = {k: r[k] for k in item.split(", ")} | {"suppressed": True, "devices": f"<{self.min_devices}"}
                sup += 1
            rows.append(r)
        base, _ = self._kpi_rows("app_crash_rate" if et == "app_crash" else "bsod_rate", [], f, a, b)
        return self._envelope(metric="app_crash_rate" if et == "app_crash" else "bsod_rate",
                              period=self._period(a, b), filters=f, rows=rows, suppressed=sup,
                              slice_total=base, warnings=self._warnings(a, b, f),
                              notes=[f"Top {n} by event count. A spike concentrated in one item and week points "
                                     "to that item; it is still a hypothesis until checked with the owning team."])
