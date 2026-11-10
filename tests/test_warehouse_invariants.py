"""Invariants on the real built warehouse. Skipped until `python -m pipeline.run` has run."""

import duckdb
import pytest

from pipeline import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel.duckdb not built")


@pytest.fixture(scope="module")
def db():
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    yield con
    con.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_telemetry_accounting(db):
    bronze = one(db, "SELECT count(*) FROM bronze_telemetry_lines")
    kept = one(db, "SELECT count(*) FROM silver_telemetry_event")
    held = one(db, "SELECT count(*) FROM quarantine WHERE source = 'telemetry' AND NOT retained")
    assert bronze == kept + held


def test_intune_accounting(db):
    bronze = one(db, "SELECT count(*) FROM bronze_intune_devices")
    enrol = one(db, "SELECT count(*) FROM silver_intune_enrollment")
    held = one(db, "SELECT count(*) FROM quarantine WHERE source = 'intune'")
    assert bronze == enrol + held


def test_keys_unique_and_standard(db):
    assert one(db, "SELECT count(*) - count(DISTINCT device_key) FROM silver_device") == 0
    assert one(db, "SELECT count(*) - count(DISTINCT event_id) FROM silver_telemetry_event") == 0
    assert one(db, "SELECT count(*) - count(DISTINCT device_key) FROM silver_rollout_tracker") == 0
    assert one(db, r"""SELECT count(*) FROM silver_device
                      WHERE NOT regexp_full_match(device_key, '(ROS|GQB|DBN)-(OF|EN|SF)-\d{5}')""") == 0


def test_durations_in_range(db):
    assert one(db, "SELECT count(*) FROM silver_telemetry_event WHERE boot_s <= 0 OR boot_s > 600") == 0
    assert one(db, "SELECT count(*) FROM silver_telemetry_event WHERE logon_s <= 0 OR logon_s > 600") == 0


def test_events_inside_window(db):
    lo, hi = db.execute("SELECT min(event_date), max(event_date) FROM silver_telemetry_event").fetchone()
    assert lo >= config.WINDOW_START and hi <= config.WINDOW_END


def test_every_dq_check_logged(db):
    assert one(db, "SELECT count(*) FROM dq_log") >= 40
    assert one(db, "SELECT count(*) FROM dq_log WHERE action_taken IS NULL OR action_taken = ''") == 0


def test_fleet_kpis_present_every_week(db):
    assert one(db, """SELECT count(*) FROM gold_kpi_weekly
                      WHERE plant_code = 'ALL' AND device_class = 'ALL' AND model = 'ALL'
                        AND os_release = 'ALL'""") == 8
