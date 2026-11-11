"""Live replay: plays the historical raw files into a landing zone on a simulated clock, the
way the source systems would deliver them, so the refresh automation and freshness checks
can be demonstrated.

    python -m replay.simulator --start 2026-10-01 --minutes-per-day 2
    python -m replay.simulator --once            # advance one simulated day and exit

Deliveries (SAST, simulated):
  telemetry      the day's file at 02:00 the next morning; the truncated 28 Sep upload is
                 followed by its _resend file a day later
  daily exports  service desk, changes, MES line stops at 06:00, as-of snapshots: nothing
                 from the future (no later rows, resolutions, actual dates or actions)
  weekly         Intune inventory and groups, rollout tracker, MES masters, remediation
                 register on Mondays at 06:00 (and on the first day)

The simulated clock is written to <state>/clock.json; arrivals to <landing>/_arrivals.json."""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline import config
from pipeline.standardise import parse_day, parse_local_datetime

SOURCE = config.ROOT / "raw"                       # the historical files, never modified
LANDING = Path(os.environ.get("KESTREL_LANDING", config.ROOT / "landing"))
FIRST_DAY, LAST_DAY = date(2026, 8, 31), date(2026, 10, 25)
TICK_HOUR = 6

WEEKLY = ["intune/kestrel_intune_devices_20261026.csv", "intune/group_membership_20261026.csv",
          "plant_it/Win11_25H2_rollout_tracker.csv", "mes/production_lines.csv", "mes/line_terminals.csv"]


def clock_path() -> Path:
    return config.STATE_DIR / "clock.json"


def read_clock() -> datetime | None:
    p = clock_path()
    return datetime.fromisoformat(json.loads(p.read_text())["now"]) if p.exists() else None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)                                  # readers never see half a file


# --------------------------------------------------------------------------- as-of filters

def _rows(rel: str):
    with open(SOURCE / rel, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        return next(r), list(r)


def _write_csv(rel: str, header, rows, bom=False) -> None:
    out = LANDING / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(out)


def _after(value: str, now: datetime) -> bool:
    t = parse_local_datetime(value)
    return t is not None and t > now


def incidents_as_of(now: datetime):
    header, rows = _rows("itsm/servicedesk_incidents_20261026.csv")
    i = {c: k for k, c in enumerate(header)}
    out = []
    for r in rows:
        opened = parse_local_datetime(r[i["opened_at"]])
        if opened is None or opened > now:
            continue
        r = list(r)
        if _after(r[i["first_response_at"]], now):
            r[i["first_response_at"]] = ""
        if _after(r[i["resolved_at"]], now) or _after(r[i["closed_at"]], now):
            if _after(r[i["resolved_at"]], now):
                r[i["resolved_at"]], r[i["close_code"]], r[i["state"]] = "", "", "In Progress"
            else:
                r[i["state"]] = "Resolved"
            r[i["closed_at"]] = ""
        r[i["sys_updated_on"]] = r[i["resolved_at"]] or r[i["first_response_at"]] or r[i["opened_at"]]
        out.append(r)
    return header, out


def changes_as_of(now: datetime):
    header, rows = _rows("change/change_requests_20261026.csv")
    i = {c: k for k, c in enumerate(header)}
    out = []
    for r in rows:
        planned = parse_local_datetime(r[i["planned_start"]])
        # Normal and standard changes are on the schedule a week ahead; emergency changes
        # only exist once they start.
        horizon = timedelta(0) if r[i["type"]].strip().lower() == "emergency" else timedelta(days=7)
        if planned is None or planned > now + horizon:
            continue
        r = list(r)
        if not r[i["actual_start"]] or _after(r[i["actual_start"]], now):
            if r[i["state"]] != "Cancelled":
                r[i["state"]], r[i["close_code"]] = "Scheduled", ""
            r[i["actual_start"]] = r[i["actual_end"]] = ""
        elif _after(r[i["actual_end"]], now):
            r[i["actual_end"]], r[i["state"]], r[i["close_code"]] = "", "Implement", ""
        out.append(r)
    return header, out


def stops_as_of(now: datetime):
    header, rows = _rows("mes/line_downtime_events.csv")
    i = {c: k for k, c in enumerate(header)}
    return header, [r for r in rows if (e := parse_local_datetime(r[i["end_local"]])) is not None and e <= now]


def remediation_as_of(now: datetime):
    header, rows = _rows("remediation/remediation_register.csv")
    i = {c: k for k, c in enumerate(header)}
    out = []
    for r in rows:
        raised = parse_day(r[i["date_raised"]])
        if raised is None or raised > now.date():
            continue
        r = list(r)
        actioned = parse_day(r[i["date_actioned"]])
        if actioned is not None and actioned > now.date():
            r[i["date_actioned"]], r[i["verified"]], r[i["status"]] = "", "", "In Progress"
        out.append(r)
    return header, out


# --------------------------------------------------------------------------- delivery

def _telemetry_due(now: datetime) -> list[tuple[str, datetime]]:
    """(file name, arrival time) for telemetry files that have landed by `now`."""
    due = []
    for f in sorted((SOURCE / "telemetry").glob("*.jsonl.gz")):
        m = re.search(r"_(\d{8})(_resend)?\.jsonl\.gz$", f.name)
        d = datetime.strptime(m.group(1), "%Y%m%d")
        arrive = d + timedelta(days=2 if m.group(2) else 1, hours=2)
        if arrive <= now:
            due.append((f.name, arrive))
    return due


def deliver(now: datetime) -> list[str]:
    """Bring the landing zone up to `now`. Returns the files written this call."""
    arrivals_path = LANDING / "_arrivals.json"
    arrivals = json.loads(arrivals_path.read_text()) if arrivals_path.exists() else {}
    written = []

    for name, at in _telemetry_due(now):
        if name not in arrivals:
            dst = LANDING / "telemetry" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / "telemetry" / name, dst)
            arrivals[name] = at.isoformat()
            written.append(f"telemetry/{name}")

    for rel, (header, rows), bom in (
            ("itsm/servicedesk_incidents_20261026.csv", incidents_as_of(now), True),
            ("change/change_requests_20261026.csv", changes_as_of(now), False),
            ("mes/line_downtime_events.csv", stops_as_of(now), False)):
        _write_csv(rel, header, rows, bom)
        arrivals[Path(rel).name] = now.isoformat()
        written.append(rel)

    if now.weekday() == 0 or Path(WEEKLY[0]).name not in arrivals:
        for rel in WEEKLY:
            dst = LANDING / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / rel, dst)
            arrivals[Path(rel).name] = now.isoformat()
            written.append(rel)
        header, rows = remediation_as_of(now)
        _write_csv("remediation/remediation_register.csv", header, rows)
        arrivals["remediation_register.csv"] = now.isoformat()
        written.append("remediation/remediation_register.csv")

    _write_json(arrivals_path, arrivals)
    return written


def set_clock(now: datetime, minutes_per_day: float | None = None) -> None:
    _write_json(clock_path(), {"now": now.isoformat(), "minutes_per_day": minutes_per_day,
                               "real_time": datetime.now().isoformat(timespec="seconds")})


def reset() -> None:
    shutil.rmtree(LANDING, ignore_errors=True)
    clock_path().unlink(missing_ok=True)


def step(now: datetime, minutes_per_day: float | None = None) -> list[str]:
    written = deliver(now)
    set_clock(now, minutes_per_day)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", type=date.fromisoformat, help="reset the landing zone and catch up to this day")
    ap.add_argument("--until", type=date.fromisoformat, default=LAST_DAY + timedelta(days=1))
    ap.add_argument("--minutes-per-day", type=float, default=2.0, help="real minutes per simulated day")
    ap.add_argument("--once", action="store_true", help="advance one simulated day and exit")
    args = ap.parse_args(argv)

    if args.start:
        if not FIRST_DAY + timedelta(days=1) <= args.start <= LAST_DAY + timedelta(days=1):
            print(f"--start must be between {FIRST_DAY + timedelta(days=1)} and {LAST_DAY + timedelta(days=1)}",
                  file=sys.stderr)
            return 2
        reset()
        now = datetime.combine(args.start, datetime.min.time()) + timedelta(hours=TICK_HOUR)
    else:
        last = read_clock()
        now = (last + timedelta(days=1)) if last else \
            datetime.combine(FIRST_DAY + timedelta(days=1), datetime.min.time()) + timedelta(hours=TICK_HOUR)

    while True:
        written = step(now, args.minutes_per_day)
        print(f"{now:%a %d %b %Y %H:%M} simulated: {len(written)} files landed", flush=True)
        if args.once or now.date() >= args.until:
            return 0
        time.sleep(args.minutes_per_day * 60)
        now += timedelta(days=1)


if __name__ == "__main__":
    sys.exit(main())
