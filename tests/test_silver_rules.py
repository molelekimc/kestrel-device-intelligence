"""End-to-end cleaning rules on the miniature landing zone in conftest.py."""

from datetime import date, datetime, timedelta

import pytest

from tests.conftest import A0, A1


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def reasons(con, source):
    return dict(con.execute(
        "SELECT reason, count(*) FROM quarantine WHERE source = ? GROUP BY 1", [source]).fetchall())


# ----------------------------------------------------------------- telemetry

def test_row_accounting_never_drops_silently(mini_db):
    bronze = one(mini_db, "SELECT count(*) FROM bronze_telemetry_lines")
    kept = one(mini_db, "SELECT count(*) FROM silver_telemetry_event")
    held = one(mini_db, "SELECT count(*) FROM quarantine WHERE source = 'telemetry' AND NOT retained")
    assert bronze == kept + held == 10


def test_telemetry_quarantine_reasons(mini_db):
    assert reasons(mini_db, "telemetry") == {
        "malformed_json": 1,
        "superseded_by_resend": 1,
        "duplicate_event_id": 1,
        "orphan_device": 1,
        "impossible_boot_duration": 1,
    }


def test_resend_replaces_partial_file(mini_db):
    files = {r[0] for r in mini_db.execute(
        "SELECT DISTINCT source_file FROM silver_telemetry_event").fetchall()}
    assert "kestrel_reliability_20260902.jsonl.gz" not in files
    assert one(mini_db, "SELECT count(*) FROM silver_telemetry_event WHERE event_id = 'e5'") == 1


def test_event_ids_unique(mini_db):
    assert one(mini_db, "SELECT count(*) - count(DISTINCT event_id) FROM silver_telemetry_event") == 0


def test_both_schemas_conform(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT event_id, event_type, schema_version, boot_s, logon_s, os_build
        FROM silver_telemetry_event""").fetchall()}
    assert rows["e1"] == ("startup", "2.3", 30.0, 20.0, "10.0.26100.6584")      # ms -> s
    assert rows["e6"] == ("startup", "2.4", 41.5, 12.25, "10.0.26100.6584")     # sec, build prefixed
    assert rows["e5"][0] == "app_crash" and rows["e7"][0] == "bugcheck"


def test_timestamps_to_utc_and_sast_day(mini_db):
    ts = dict(mini_db.execute(
        "SELECT event_id, event_ts_utc FROM silver_telemetry_event").fetchall())
    assert ts["e1"] == datetime(2026, 9, 1, 6, 0)          # 'Z'
    assert ts["e2"] == datetime(2026, 9, 1, 5, 0)          # '+02:00' shifted to UTC
    assert ts["e6"] == datetime(2026, 9, 1, 22, 30)        # epoch ms
    # 22:30 UTC is 00:30 SAST the next day, which is the file the agent put it in.
    assert one(mini_db, "SELECT event_date FROM silver_telemetry_event WHERE event_id = 'e6'") == date(2026, 9, 2)
    assert one(mini_db, "SELECT iso_week FROM silver_telemetry_event WHERE event_id = 'e6'") == "2026-W36"


def test_impossible_duration_nulled_but_startup_kept(mini_db):
    boot, logon = mini_db.execute(
        "SELECT boot_s, logon_s FROM silver_telemetry_event WHERE event_id = 'e2'").fetchone()
    assert boot is None and logon is None
    assert one(mini_db, "SELECT retained FROM quarantine WHERE reason = 'impossible_boot_duration'") is True


def test_superseded_device_id_maps_to_current_device(mini_db):
    assert one(mini_db, "SELECT device_key FROM silver_telemetry_event WHERE event_id = 'e4'") == "ROS-OF-00001"
    assert one(mini_db, "SELECT aad_device_id FROM silver_telemetry_event WHERE event_id = 'e4'") == A0


# ----------------------------------------------------------------- intune

def test_reenrolled_device_collapses_to_latest(mini_db):
    assert one(mini_db, "SELECT count(*) FROM silver_device WHERE device_key = 'ROS-OF-00001'") == 1
    assert one(mini_db, "SELECT aad_device_id FROM silver_device WHERE device_key = 'ROS-OF-00001'") == A1
    assert one(mini_db, "SELECT count(*) FROM silver_device_id_map WHERE device_key = 'ROS-OF-00001'") == 2


def test_intune_conforming(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT device_key, plant_code, model, intune_os_release, on_win10_build, compliance, storage_gb, lifecycle
        FROM silver_device""").fetchall()}
    assert rows["ROS-OF-00001"] == ("ROS", "ThinkPad T14 Gen 4", "Win11 24H2", False, "Compliant", 512, "active")
    assert rows["GQB-SF-00002"] == ("GQB", "ThinkCentre M70q Gen 3", "Win10 LTSC 21H2", True, "Compliant", 256, "active")
    assert rows["DBN-EN-00003"] == ("DBN", "Precision 3660", "Win11 25H2", False, None, 1024, "stale_retired")


def test_intune_dq_log_written(mini_db):
    log = dict(mini_db.execute("SELECT check_name, rows_affected FROM dq_log").fetchall())
    assert log["intune_reenrolled_superseded"] == 1
    assert log["intune_plant_category_missing"] == 1
    assert log["intune_model_lenovo_mtm"] == 2
    assert log["intune_stale_retired"] == 1


# ----------------------------------------------------------------- tracker

def test_tracker_rows(mini_db):
    assert reasons(mini_db, "tracker") == {
        "title_or_header_row": 4, "blank_row": 1, "footer_total_row": 1, "superseded_duplicate_host": 1}
    rows = {r[0]: r[1:] for r in mini_db.execute(
        "SELECT device_key, wave, status, scheduled_date FROM silver_rollout_tracker").fetchall()}
    assert rows["ROS-OF-00001"] == ("Wave 3", "Done", date(1899, 12, 30) + timedelta(days=46300))
    assert rows["GQB-SF-00002"] == ("Exempt", "Exempt", None)   # 'O' typo fixed in hostname


# ----------------------------------------------------------------- gold

def test_device_week(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT device_key, startups, app_crashes, bugchecks, intune_active, reporting
        FROM gold_device_week WHERE iso_week = '2026-W36'""").fetchall()}
    assert rows["ROS-OF-00001"] == (3, 1, 0, True, True)
    assert rows["GQB-SF-00002"] == (1, 0, 1, True, True)
    assert "DBN-EN-00003" not in rows        # retired before the window and silent


def test_rollout_reconciliation(mini_db):
    status = dict(mini_db.execute(
        "SELECT device_key, reconciliation_status FROM gold_rollout_reconciliation").fetchall())
    assert status == {
        "ROS-OF-00001": "unverifiable_stale_telemetry",   # tracker Done, telemetry ends before schedule
        "GQB-SF-00002": "exempt_ok",
        "DBN-EN-00003": "no_telemetry",
    }


@pytest.mark.parametrize("ts, expected", [
    ("2026-09-01T06:00:00Z", datetime(2026, 9, 1, 6)),
    ("2026-09-01T06:00:00+02:00", datetime(2026, 9, 1, 4)),
    ("2026-09-01T06:00:00-01:30", datetime(2026, 9, 1, 7, 30)),
    ("01/09/2026 06:00", None),
])
def test_utc_from_iso_macro(mini_db, ts, expected):
    assert one(mini_db, "SELECT utc_from_iso(?)", [ts]) == expected
