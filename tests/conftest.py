"""A miniature raw landing zone that exercises every Silver rule, built fresh per test session."""

import csv
import gzip
import json
import shutil
from datetime import datetime, timezone

import duckdb
import pytest

from pipeline import bronze, config, dq, gold, silver

INTUNE_COLS = ["Device ID", "Device name", "Device category", "Managed by", "Ownership", "Compliance",
               "OS", "OS version", "Last check-in", "Enrollment date", "Serial number", "Manufacturer",
               "Model", "Primary user UPN", "Azure AD Device ID", "Total storage"]

A1 = "aaaaaaaa-0000-4000-8000-000000000001"   # ROS-OF-00001, current enrolment
A0 = "aaaaaaaa-0000-4000-8000-000000000000"   # ROS-OF-00001, superseded enrolment
B1 = "bbbbbbbb-0000-4000-8000-000000000001"   # GQB-SF-00002, LTSC kiosk
C1 = "cccccccc-0000-4000-8000-000000000001"   # DBN-EN-00003, stale / retired
Z9 = "99999999-0000-4000-8000-000000000009"   # orphan, not in Intune


def epoch_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


def ev23(eid, etype, aad, host, ts, **payload):
    return json.dumps({"event_id": eid, "event_type": etype, "agent_version": "2.3.7",
                       "device": {"deviceId": aad, "computer_name": host}, "ts": ts, "payload": payload})


def ev24(eid, etype, aad, fqdn, iso, **data):
    return json.dumps({"eventId": eid, "eventType": etype, "agentVersion": "2.4.1",
                       "deviceId": "{" + aad.upper() + "}", "fqdn": fqdn,
                       "timestamp": epoch_ms(iso), "data": data})


def write_raw(root):
    (root / "intune").mkdir(parents=True)
    (root / "telemetry").mkdir()
    (root / "plant_it").mkdir()

    rows = [
        ["i-1", "ROS-OF-00001", "Rosslyn Plant", "Intune", "Corporate", "Compliant", "Windows",
         "10.0.26100.6584", "2026-10-25 10:00:00", "2025-01-01 09:00:00", "PF0TST01", "Lenovo",
         "Lenovo ThinkPad T14 Gen 4", "user.one@kestrel-ag.example", A1, "476.83 GB"],
        ["i-0", " ros-of-00001 ", "rosslyn", "Intune", "Corporate", "noncompliant", "Windows",
         "26100.6584", "3/15/2026 10:00:00 AM", "01/02/2024 10:00", "PF0TST01", "Lenovo",
         "21HD5852ZA", "user.one@kestrel-ag.example", A0, "512"],
        ["i-2", "GQB-SF-00002", "PE Plant", "Co-managed", "Corporate", "Compliant", "Windows",
         "19044.6332", "10/24/2026 08:00:00 PM", "2024-06-01 08:00:00", "PF0TST02", "Lenovo",
         "11T33300ZA", "", B1, "256 GB"],
        ["i-3", "DBN-EN-00003", "", "Intune", "Corporate", "null", "Windows",
         "10.0.26200.6901", "2026-07-01 08:00:00", "2023-03-01 08:00:00", "PF0TST03", "Dell",
         "Precision 3660", "user.three@kestrel-ag.example", C1, "1024 GB"],
    ]
    with open(root / "intune" / "kestrel_intune_devices_20261026.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(INTUNE_COLS)
        w.writerows(rows)

    day1 = [
        ev23("e1", "startup", A1, "ROS-OF-00001", "2026-09-01T06:00:00Z",
             os_build="10.0.26100.6584", boot_duration_ms=30000, logon_duration_ms=20000),
        ev23("e1", "startup", A1, "ROS-OF-00001", "2026-09-01T06:00:00Z",          # duplicate
             os_build="10.0.26100.6584", boot_duration_ms=30000, logon_duration_ms=20000),
        ev23("e2", "startup", B1, "GQB-SF-00002", "2026-09-01T07:00:00+02:00",     # = 05:00 UTC
             os_build="10.0.19044.6332", boot_duration_ms=-5),                   # impossible; kiosk, no logon
        '{"event_id":"e-bad","event_type":"startup","agent_ver',                  # truncated
        ev23("e3", "startup", Z9, "ROS-LAB-0001", "2026-09-01T08:00:00Z",          # orphan
             os_build="10.0.26100.6584", boot_duration_ms=25000, logon_duration_ms=15000),
        ev23("e4", "startup", A0, "ROS-OF-00001", "2026-09-01T09:00:00Z",          # pre-re-enrolment id
             os_build="10.0.26100.6584", boot_duration_ms=50000, logon_duration_ms=40000),
    ]
    partial = [
        ev24("e5", "AppCrash", A1, "ros-of-00001.kestrel.local", "2026-09-02T10:00:00",
             osBuild="26100.6584", appName="KestrelMES.exe", appVersion="4.2.1"),
    ]
    resend = partial + [
        ev24("e6", "Startup", A1, "ros-of-00001.kestrel.local", "2026-09-01T22:30:00",  # 00:30 SAST on 2 Sep
             osBuild="26100.6584", bootDurationSec=41.5, logonDurationSec=12.25),
        ev24("e7", "BugCheck", B1, "gqb-sf-00002.kestrel.local", "2026-09-02T11:00:00",
             osBuild="19044.6332", bugCheckCode="0x00000116"),
    ]
    for name, lines in (("kestrel_reliability_20260901.jsonl.gz", day1),
                        ("kestrel_reliability_20260902.jsonl.gz", partial),
                        ("kestrel_reliability_20260902_resend.jsonl.gz", resend)):
        with gzip.open(root / "telemetry" / name, "wt") as f:
            f.write("\n".join(lines) + "\n")

    tracker = [
        ["Kestrel Assembly Group - Windows 11 25H2 Rollout Tracker", "", "", "", "", "", ""],
        ["Owner: Plant IT | Last updated: 24/10/2026", "", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["Hostname", "Plant", "Wave", "Scheduled Date", "Status", "Updated By", "Comments"],
        ["ROS-OF00001", "Rosslyn", "W2", "15/09/2026", "Rescheduled", "TM", "moved to W3"],
        ["ROS-OF-00001", "Rosslyn", "wave 3 ", "46300", "Completed", "KP", ""],
        ["GQB-SF-0O002", "Port Elizabeth", "N/A", "", "Exempt - LTSC", "SN", ""],
        ["", "", "", "", "", "", ""],
        ["TOTAL", "", "", "", "2 devices", "", ""],
    ]
    with open(root / "plant_it" / "Win11_25H2_rollout_tracker.csv", "w", newline="") as f:
        csv.writer(f).writerows(tracker)


@pytest.fixture(scope="session")
def mini_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("raw")
    write_raw(root)
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "INTUNE_GLOB", str(root / "intune" / "*.csv"))
    mp.setattr(config, "TELEMETRY_GLOB", str(root / "telemetry" / "*.jsonl.gz"))
    mp.setattr(config, "TRACKER_PATH", str(root / "plant_it" / "Win11_25H2_rollout_tracker.csv"))
    con = duckdb.connect(str(root / "mini.duckdb"))
    dq.create_tables(con)
    bronze.build(con)
    silver.build(con)
    gold.build(con)
    con.execute("CHECKPOINT")
    # A second copy for read-only tool tests; DuckDB will not open one file
    # read-write and read-only in the same process.
    shutil.copy(root / "mini.duckdb", root / "mini_ro.duckdb")
    yield con
    con.close()
    mp.undo()


@pytest.fixture(scope="session")
def mini_db_path(mini_db, tmp_path_factory):
    return next(tmp_path_factory.getbasetemp().glob("raw*/mini_ro.duckdb"))


@pytest.fixture(autouse=True)
def audit_to_tmp(tmp_path, monkeypatch):
    from mcp_server import audit
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    return tmp_path / "audit.jsonl"
