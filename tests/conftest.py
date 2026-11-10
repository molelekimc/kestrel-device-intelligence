"""A miniature raw landing zone that exercises every Silver rule, built fresh per test session."""

import csv
import gzip
import json
import shutil
from datetime import datetime, timezone

import duckdb
import pytest

from pipeline import bronze, config, dq, freshness, gold, gold_business, silver, silver_business

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


def write_business(root):
    """Miniature business sources that exercise every business Silver rule."""
    def w(rel, header, rows, bom=False):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
            csv.writer(f).writerows([header, *rows])
        return str(path)

    paths = {}
    paths["bronze_itsm_incidents"] = w("itsm/inc.csv", [
        "number", "opened_at", "first_response_at", "resolved_at", "closed_at", "state", "priority", "category",
        "short_description", "caller_id", "location", "cmdb_ci", "assignment_group", "parent_incident",
        "problem_id", "caused_by_change", "close_code", "reopen_count", "sys_updated_on"], [
        # INC1: reassigned, exported twice; the later row wins. CI written as an FQDN.
        ["INC1", "2026-09-01 08:00:00", "2026-09-01 08:30:00", "2026-09-01 12:00:00", "", "Resolved", "2 - High",
         "BSOD", "blue screen on ros-of-00001", "Test Caller", "Rosslyn", "ros-of-00001.kestrel.local",
         "EUC Engineering", "", "PRB1", "", "Solved (Workaround)", "1", "2026-09-01 12:00:00"],
        ["INC1", "2026-09-01 08:00:00", "2026-09-01 08:30:00", "", "", "In Progress", "2 - High",
         "BSOD", "blue screen on ros-of-00001", "Test Caller", "Rosslyn", "ros-of-00001.kestrel.local",
         "Service Desk L1", "", "", "", "", "0", "2026-09-01 08:30:00"],
        # INC2: resolved before opened (day-first dates); no CI, plant from location alias.
        ["INC2", "02/09/2026 09:00:00", "02/09/2026 09:10:00", "01/09/2026 09:00:00", "", "Closed", "P3",
         "Slow PC", "slow", "Other Person", "PE Plant", "", "Service Desk L1", "", "", "", "Solved (Permanently)",
         "0", "02/09/2026 09:00:00"],
        # INC3: P1 major incident, resolved in 8 h (SLA 4 h).
        ["INC3", "2026-09-02 07:00:00", "2026-09-02 07:05:00", "2026-09-02 15:00:00", "", "Resolved", "P1",
         "MES", "MES down", "Service Desk (monitoring)", "Rosslyn", "", "MES Support", "", "PRB2", "CHG1",
         "Solved (Permanently)", "0", "2026-09-02 15:00:00"],
        # INC4: child of INC3.
        ["INC4", "2026-09-02 07:30:00", "2026-09-02 07:40:00", "2026-09-02 15:00:00", "", "Resolved", "High",
         "KestrelMES", "MES crashed on GQB-SF-00002", "Line Lead", "Gqeberha", "GQB-SF-00002", "MES Support",
         "INC3", "PRB2", "", "Duplicate", "0", "2026-09-02 15:00:00"],
    ], bom=True)
    paths["bronze_change_requests"] = w("change/chg.csv", [
        "number", "type", "category", "short_description", "assignment_target", "risk", "planned_start",
        "planned_end", "actual_start", "actual_end", "state", "close_code", "problem_id", "requested_by"], [
        ["CHG1", "Standard", "Intune - App deployment", "Deploy KestrelMES client 4.2.0", "grp-mes-users ", "Low",
         "2026-09-02 05:00:00", "2026-09-02 09:00:00", "2026-09-02 05:00:00", "2026-09-02 06:00:00", "Closed",
         "Successful", "", "TM"],
        ["CHG2", "Normal", "Intune - Configuration profile", "BitLocker change", "GRP OF", "Low",
         "02/09/2026 08:00", "02/09/2026 12:00", "", "", "Cancelled", "", "", "SN"],
    ])
    paths["bronze_group_membership"] = w("intune/groups.csv", ["group_name", "member_name", "member_type"], [
        ["GRP-MES-USERS", "GQB-SF-00002", "Device"], ["GRP-MES-USERS", "ros-of-00001 ", "Device"],
        ["GRP-OF", "ROS-OF-00001", "Device"], ["GRP-OF", "ROS-OF-99999", "Device"]])
    paths["bronze_mes_lines"] = w("mes/lines.csv", ["line_id", "mes_plant", "site_name", "product_family",
                                                    "standard_units_per_hour", "shift_pattern"],
                                  [["P02-AXLE-L01", "P02", "PE", "AXLE", "30", "3x8"]])
    paths["bronze_mes_terminals"] = w("mes/terms.csv", ["line_id", "station", "terminal_name", "line_critical"],
                                      [["P02-AXLE-L01", "ST-001", "gqb-sf-00002.kestrel.local", "Y"]])
    paths["bronze_mes_downtime"] = w("mes/stops.csv", [
        "event_id", "mes_plant", "line", "station", "start_local", "end_local", "duration_min", "reason_code",
        "shift", "operator_comment"], [
        ["DT1", "P02", "P02-AXLE-L01", "ST-001", "2026-09-02 08:00", "2026-09-02 08:10", "10", "IT-APP", "A", "MES crashed"],
        ["DT1D", "P02", "P02-AXLE-L01", "ST-001", "2026-09-02 08:00", "2026-09-02 08:12", "10", "IT-APP", "A", "MES crashed"],
        ["DT2", "P02", "P02-AXLE-L01", "ST-001", "02/09/2026 10:00", "02/09/2026 10:30", "0.5", "OTHER", "A", "screen froze"],
        ["DT3", "P02", "P02-AXLE-L01", "", "2026-09-02T11:00:00", "2026-09-02T11:45:00", "45", "MAT", "A", "no parts"],
        ["DT4", "P02", "AXLE L01", "", "2026-09-03 09:00", "2026-09-03 09:20", "20", "MECH", "A", "robot fault"],
    ])
    paths["bronze_remediation"] = w("remediation/reg.csv", [
        "action_id", "problem_id", "title", "linked_change", "owner", "target", "status", "date_raised",
        "due_date", "date_actioned", "verified", "expected_benefit", "comments"], [
        ["RA-900", "PRB9", "Apply BitLocker change", "CHG2", "KP", "Office", "done ", "01/09/2026", "2026-09-05",
         "05 Sep 2026", "", "", ""],
        ["RA-901", "PRB2", "Investigate MES", "", "SN", "MES", "In Progress", "2026-09-02", "2026-09-10", "", "", "", ""],
    ])
    return paths


@pytest.fixture(scope="session")
def mini_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("raw")
    write_raw(root)
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "INTUNE_GLOB", str(root / "intune" / "kestrel_intune_devices_*.csv"))
    mp.setattr(config, "TELEMETRY_GLOB", str(root / "telemetry" / "*.jsonl.gz"))
    mp.setattr(config, "TRACKER_PATH", str(root / "plant_it" / "Win11_25H2_rollout_tracker.csv"))
    mp.setattr(config, "BUSINESS_SOURCES", write_business(root))
    mp.setattr(config, "RAW", root)
    con = duckdb.connect(str(root / "mini.duckdb"))
    dq.create_tables(con)
    bronze.build(con)
    silver.build(con)
    silver_business.build(con)
    gold.build(con)
    gold_business.build(con)
    freshness.build(con)
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
