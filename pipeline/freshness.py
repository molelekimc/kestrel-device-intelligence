"""gold_freshness: how current each source is against its SLA (metrics/freshness.yaml).

During a live replay the simulator records when each file arrived (simulated time) in
<source root>/_arrivals.json and passes its clock as KESTREL_AS_OF. Outside a replay,
arrival times are unknown: event feeds are judged by their newest event, snapshot
exports are assumed current as of the run."""

import json
from datetime import datetime, timedelta

import yaml

from pipeline import config, dq

STATUS_RANK = {"fresh": 0, "late": 1, "stale": 2, "missing": 3}


def load_config() -> dict:
    with open(config.FRESHNESS_PATH) as f:
        return yaml.safe_load(f)


def age_status(age_hours: float | None, expected: float, grace: float, late_factor: float) -> str:
    if age_hours is None:
        return "missing"
    if age_hours <= expected + grace:
        return "fresh"
    if age_hours <= late_factor * expected + grace:
        return "late"
    return "stale"


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: STATUS_RANK[s])


def _arrivals() -> dict[str, datetime]:
    path = config.RAW / "_arrivals.json"
    if not path.exists():
        return {}
    return {k: datetime.fromisoformat(v) for k, v in json.loads(path.read_text()).items()}


def _hours(now: datetime, then: datetime | None) -> float | None:
    return None if then is None else round((now - then).total_seconds() / 3600, 1)


def build(con) -> None:
    cfg = load_config()
    late_factor = cfg["bands"]["late_factor"]
    now = config.AS_OF
    arrivals = _arrivals()
    rows = []
    for key, s in cfg["sources"].items():
        files = sorted(config.RAW.glob(s["files"]))
        arrived = [arrivals[f.name] for f in files if f.name in arrivals]
        latest_event = con.execute(s["latest_event_sql"]).fetchone()[0] if s.get("latest_event_sql") else None
        if not files:
            as_of, basis = None, "no file has arrived"
        elif arrived:
            as_of, basis = max(arrived), "file arrival"
        elif s.get("event_feed"):
            as_of, basis = latest_event, "newest event (arrival time unknown)"
        else:
            as_of, basis = now, "export assumed current (arrival time unknown)"
        age = _hours(now, as_of)
        status = age_status(age, s["expected_hours"], s["grace_hours"], late_factor)
        rows.append([key, "ALL", s["label"], as_of, latest_event, s["expected_hours"], s["grace_hours"], age,
                     status, basis])
        if s.get("per_plant"):
            rows += _per_plant(con, key, s, now, late_factor)

    con.execute("""
        CREATE OR REPLACE TABLE gold_freshness (
            source VARCHAR, scope VARCHAR, label VARCHAR, as_of TIMESTAMP, latest_event_at TIMESTAMP,
            expected_hours DOUBLE, grace_hours DOUBLE, age_hours DOUBLE, status VARCHAR, reason VARCHAR,
            checked_at TIMESTAMP)""")
    con.executemany("INSERT INTO gold_freshness VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [r + [now] for r in rows])
    dq.log_count(con, "freshness_not_fresh", "gold_freshness", "status <> 'fresh'",
                 "source or plant feed behind its SLA (see gold_freshness)", "warning")


def _per_plant(con, key, s, now, late_factor) -> list:
    """Telemetry per plant: age of the newest event, and whether the latest weekday that has
    landed carried a normal volume of devices (an outage still delivers files, just empty ones)."""
    out = []
    latest = dict(con.execute("""
        SELECT d.plant_code, max(e.event_ts_sast) FROM silver_telemetry_event e
        JOIN silver_device d USING (device_key) GROUP BY 1""").fetchall())
    last_day = config.WINDOW_END
    weekday = last_day - timedelta(days=max(0, last_day.weekday() - 4))     # Sat/Sun -> Friday
    vol = dict(con.execute("""
        SELECT plant_code, reporting_vs_weekday_median FROM gold_coverage_daily
        WHERE day = ? AND device_class = 'ALL' AND plant_code <> 'ALL'""", [weekday]).fetchall())
    for plant in ("ROS", "GQB", "DBN"):
        age = _hours(now, latest.get(plant))
        status = age_status(age, s["expected_hours"], s["grace_hours"], late_factor)
        reason = "newest event from this plant"
        ratio = vol.get(plant)
        if ratio is not None and ratio < s["volume_min_ratio"]:
            status = worst(status, "stale")
            reason = (f"{weekday:%a %d %b}: only {ratio:.0%} of a normal weekday's devices reported "
                      "(files arriving, plant missing)")
        out.append([key, plant, f"{s['label']} - {plant}", latest.get(plant), latest.get(plant),
                    s["expected_hours"], s["grace_hours"], age, status, reason])
    return out
