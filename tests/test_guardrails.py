"""Guardrails: input allowlists, small-group suppression, identifier blocking, audit log."""

import json

import pytest

from mcp_server.guardrails import IdentifierLeak, OutboundCheck, ToolInputError
from mcp_server.tools import FleetTools


@pytest.fixture(scope="module")
def mini_tools(mini_db_path):
    return FleetTools(mini_db_path, caller="test")


# ----------------------------------------------------------------- input validation

@pytest.mark.parametrize("value, expected", [
    ("ROS", "ROS"), ("rosslyn", "ROS"), ("Port Elizabeth", "GQB"), ("durban", "DBN"),
])
def test_plant_aliases(mini_tools, value, expected):
    assert mini_tools.v.plant(value) == expected


@pytest.mark.parametrize("value", [
    "ROS; DROP TABLE silver_device", "ROS' OR '1'='1", "*", "Cape Town", "",
])
def test_plant_rejects_anything_else(mini_tools, value):
    with pytest.raises(ToolInputError, match="Allowed: DBN, GQB, ROS"):
        mini_tools.v.plant(value)


@pytest.mark.parametrize("value", ["2026-W99", "2026-41", "W41", "2026-W41; --", "2025-W41"])
def test_week_must_exist(mini_tools, value):
    with pytest.raises(ToolInputError):
        mini_tools.v.week(value)


def test_week_range_and_period(mini_tools):
    assert mini_tools.v.week_range(None, None) == ("2026-W36", "2026-W43")
    assert mini_tools.v.period("2026-W36..2026-W38") == ("2026-W36", "2026-W38")
    with pytest.raises(ToolInputError):
        mini_tools.v.week_range("2026-W40", "2026-W37")


@pytest.mark.parametrize("value", ["Win11 24H2", "24h2", "26100", "10.0.26100.6584"])
def test_os_build_aliases(mini_tools, value):
    assert mini_tools.v.os_release(value) == "Win11 24H2"


def test_os_build_error_echoes_input(mini_tools):
    with pytest.raises(ToolInputError, match=r"Unknown os_build / os_release '23H2'\. Allowed: "):
        mini_tools.v.os_release("23H2")


def test_metric_and_limit(mini_tools):
    assert mini_tools.v.metric("BSOD_RATE") == "bsod_rate"
    with pytest.raises(ToolInputError):
        mini_tools.v.metric("select * from bronze_intune_devices")
    with pytest.raises(ToolInputError):
        mini_tools.v.limit(500)


# ----------------------------------------------------------------- suppression

def test_small_groups_suppressed(mini_tools):
    # The miniature fleet has two devices, far below the 10-device minimum.
    r = mini_tools.get_kpi("startup_count", group_by="plant")
    assert r["rows"] and all(row["suppressed"] and row["value"] is None for row in r["rows"])
    assert all(row["value"] is None for row in r["period_total"])
    assert r["suppressed_groups"] == len(r["rows"]) + len(r["period_total"])
    assert "startups" not in json.dumps(r["rows"])           # no supporting counts leak either


def test_top_events_suppressed(mini_tools):
    r = mini_tools.get_top_events("app_crash")
    assert r["rows"] == [{"app_name": "KestrelMES.exe", "app_version": "4.2.1",
                          "suppressed": True, "devices": "<10"}]


def test_rollout_suppressed(mini_tools):
    r = mini_tools.get_rollout_status()
    assert all(row["devices"] == "<10" for row in r["rows"])


# ----------------------------------------------------------------- outbound identifier check

@pytest.mark.parametrize("payload", [
    {"rows": [{"note": "ROS-OF-12345 crashed"}]},
    {"rows": [{"note": "see dbn-sf-17193.kestrel.local"}]},
    {"x": "aaaaaaaa-0000-4000-8000-000000000001"},
    {"x": ["owner: user.one@kestrel-ag.example"]},
    {"x": {"PF0TST01": 1}},                   # serial number as a key
])
def test_outbound_check_blocks_identifiers(mini_tools, payload):
    with pytest.raises(IdentifierLeak):
        mini_tools._out.check(payload)


def test_outbound_check_allows_aggregates(mini_tools):
    mini_tools._out.check({"rows": [{"model": "Elite Mini 800 G9", "bugcheck_code": "0x00000116",
                                     "app_name": "KestrelMES.exe", "plant": "ROS", "value": 4.2}]})


def test_blocked_response_never_returned(mini_tools, monkeypatch):
    real = mini_tools.get_metric_definition.__wrapped__

    def leaky(self, metric):
        out = real(self, metric)
        out["notes"].append("worst device: GQB-SF-00002")
        return out

    monkeypatch.setattr(OutboundCheck, "check", OutboundCheck.check)
    from mcp_server import tools as tools_mod
    monkeypatch.setattr(tools_mod.FleetTools, "get_metric_definition", tools_mod.audited(leaky))
    with pytest.raises(IdentifierLeak):
        mini_tools.get_metric_definition("coverage")


# ----------------------------------------------------------------- audit log

def test_every_call_audited(mini_tools, audit_to_tmp):
    mini_tools.get_kpi("coverage", plant="DBN")
    with pytest.raises(ToolInputError):
        mini_tools.get_kpi("coverage", plant="Atlantis")
    lines = [json.loads(x) for x in audit_to_tmp.read_text().splitlines()]
    assert [(e["tool"], e["ok"]) for e in lines] == [("get_kpi", True), ("get_kpi", False)]
    assert lines[0]["caller"] == "test" and lines[0]["args"] == {"metric": "coverage", "plant": "DBN"}
    assert "duration_ms" in lines[0] and lines[1]["error"].startswith("Unknown plant")


def test_connection_is_read_only(mini_tools):
    with pytest.raises(Exception, match="read-only"):
        mini_tools.con.execute("DELETE FROM dq_log")
