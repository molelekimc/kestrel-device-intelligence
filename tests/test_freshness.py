"""Freshness SLAs, the replay simulator's as-of deliveries, and the refresh watcher (Phase 7)."""

import json
from datetime import datetime

import duckdb
import pytest

from pipeline import config
from pipeline.freshness import age_status, worst
from pipeline.standardise import parse_day, parse_local_datetime
from refresh import watcher
from replay import simulator as sim

NOW = datetime(2026, 10, 6, 6, 0)


# ----------------------------------------------------------------- SLA bands

@pytest.mark.parametrize("age, expected", [
    (10, "fresh"), (30, "fresh"), (30.1, "late"), (54, "late"), (54.1, "stale"), (None, "missing")])
def test_age_status_daily_feed(age, expected):
    assert age_status(age, 24, 6, 2) == expected


def test_worst_status():
    assert worst("fresh", "late") == "late" and worst("late", "stale", "fresh") == "stale"


# ----------------------------------------------------------------- replay deliveries

def test_telemetry_arrives_next_morning_and_resend_a_day_later():
    due = dict(sim._telemetry_due(datetime(2026, 9, 2, 6, 0)))
    assert set(due) == {"kestrel_reliability_20260831.jsonl.gz", "kestrel_reliability_20260901.jsonl.gz"}
    assert due["kestrel_reliability_20260901.jsonl.gz"] == datetime(2026, 9, 2, 2, 0)
    late = dict(sim._telemetry_due(datetime(2026, 9, 30, 6, 0)))
    assert "kestrel_reliability_20260928.jsonl.gz" in late
    assert late["kestrel_reliability_20260928_resend.jsonl.gz"] == datetime(2026, 9, 30, 2, 0)


def test_incidents_have_no_future_rows_or_resolutions():
    header, rows = sim.incidents_as_of(NOW)
    i = {c: k for k, c in enumerate(header)}
    assert rows
    for r in rows:
        assert parse_local_datetime(r[i["opened_at"]]) <= NOW
        for col in ("first_response_at", "resolved_at", "closed_at"):
            t = parse_local_datetime(r[i[col]])
            assert t is None or t <= NOW, col
        if not r[i["resolved_at"]]:
            assert r[i["state"]] == "In Progress" and not r[i["close_code"]]


def test_changes_as_of_hide_the_future():
    header, rows = sim.changes_as_of(NOW)
    i = {c: k for k, c in enumerate(header)}
    by_id = {r[i["number"]]: r for r in rows}
    assert "CHG0041038" not in by_id and "CHG0041039" not in by_id       # emergency changes not yet raised
    wave3 = by_id["CHG0041035"]                                         # normal change, planned Thu 8 Oct
    assert wave3[i["state"]] == "Scheduled" and wave3[i["actual_start"]] == ""
    assert by_id["CHG0041031"][i["actual_start"]]                       # MES 4.2.0 went in on Mon 5 Oct


def test_stops_and_remediation_as_of():
    header, rows = sim.stops_as_of(NOW)
    i = {c: k for k, c in enumerate(header)}
    assert rows and all(parse_local_datetime(r[i["end_local"]]) <= NOW for r in rows)
    header, rows = sim.remediation_as_of(datetime(2026, 10, 13, 6, 0))
    i = {c: k for k, c in enumerate(header)}
    by_id = {r[i["action_id"]]: r for r in rows}
    assert "RA-002" not in by_id                                        # raised 16 Oct
    assert by_id["RA-001"][i["status"]] == "In Progress" and by_id["RA-001"][i["date_actioned"]] == ""
    assert parse_day(by_id["RA-003"][i["date_actioned"]]) is not None   # actioned 12 Oct


@pytest.fixture
def landing(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "LANDING", tmp_path / "landing")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    return tmp_path / "landing"


def test_deliver_records_arrivals_and_weekly_files(landing):
    sim.step(datetime(2026, 9, 2, 6, 0))                    # Wednesday, first delivery
    arrivals = json.loads((landing / "_arrivals.json").read_text())
    assert arrivals["kestrel_reliability_20260831.jsonl.gz"] == "2026-09-01T02:00:00"
    assert arrivals["kestrel_intune_devices_20261026.csv"] == "2026-09-02T06:00:00"   # first delivery
    sim.step(datetime(2026, 9, 3, 6, 0))                    # Thursday: weekly files not re-sent
    arrivals = json.loads((landing / "_arrivals.json").read_text())
    assert arrivals["kestrel_intune_devices_20261026.csv"] == "2026-09-02T06:00:00"
    assert arrivals["servicedesk_incidents_20261026.csv"] == "2026-09-03T06:00:00"
    assert sim.read_clock() == datetime(2026, 9, 3, 6, 0)


# ----------------------------------------------------------------- watcher

def test_manifest_detects_changes(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    m1 = watcher.manifest(tmp_path)
    (tmp_path / "b.csv").write_text("y")
    (tmp_path / "c.tmp").write_text("partial")              # in-flight writes are ignored
    m2 = watcher.manifest(tmp_path)
    assert watcher.changed_files(m1, m2) == ["b.csv"]


def test_failed_refresh_keeps_last_good_warehouse(tmp_path, monkeypatch):
    db = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE marker AS SELECT 'last good' AS v")
    con.close()
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(watcher, "LOG_PATH", tmp_path / "state" / "refresh_log.jsonl")
    empty = tmp_path / "landing"
    empty.mkdir()
    entry = watcher.refresh(empty, "test")
    assert entry["status"] == "failed" and entry["error"]
    assert duckdb.connect(str(db), read_only=True).execute("SELECT v FROM marker").fetchone()[0] == "last good"
    assert watcher.read_log()[-1]["run_id"] == entry["run_id"]


@pytest.mark.slow
def test_replay_flags_durban_on_tuesday_morning(tmp_path, monkeypatch, landing):
    """End to end: replay to Tue 6 Oct 06:00, refresh, and the Durban feed is stale."""
    sim.step(NOW)
    db = tmp_path / "replay.duckdb"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(watcher, "LOG_PATH", tmp_path / "state" / "refresh_log.jsonl")
    entry = watcher.refresh(landing, "test")
    assert entry["status"] == "ok", entry.get("error")
    con = duckdb.connect(str(db), read_only=True)
    status, reason = con.execute(
        "SELECT status, reason FROM gold_freshness WHERE source = 'telemetry' AND scope = 'DBN'").fetchone()
    assert status == "stale" and "Mon 05 Oct" in reason
    assert con.execute("SELECT max(opened_at) FROM silver_ticket").fetchone()[0] <= NOW
