"""Tool behaviour on the real warehouse, and the MCP surface itself.
Skipped until `python -m pipeline.run` has built kestrel.duckdb."""

import asyncio
import json
import re

import pytest

from pipeline import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel.duckdb not built")

ENVELOPE = {"metric_definition", "period", "filters", "rows", "suppressed_groups",
            "suppression_rule", "data_quality", "warnings", "notes"}
IDENTIFIER = re.compile(r"(ROS|GQB|DBN)-(OF|EN|SF)-\d{5}|[0-9a-f]{8}-[0-9a-f]{4}-|@kestrel", re.I)


@pytest.fixture(scope="module")
def t():
    from mcp_server.tools import FleetTools
    return FleetTools(caller="test")


CALLS = [
    ("list_allowed_values", {}),
    ("get_metric_definition", {"metric": "bsod_rate"}),
    ("get_kpi", {"metric": "boot_time_p90", "group_by": "model"}),
    ("compare_cohorts", {"metric": "logon_time_p50", "group_by": "plant",
                         "period_a": "2026-W36..2026-W38", "period_b": "2026-W42..2026-W43"}),
    ("compare_cohorts", {"metric": "boot_time_p50", "group_by": "rollout_cohort",
                         "period_a": "2026-W36..2026-W37", "period_b": "2026-W42..2026-W43"}),
    ("get_coverage", {"granularity": "day"}),
    ("get_data_quality_status", {"week": "2026-W41"}),
    ("list_threshold_breaches", {"week": "2026-W41"}),
    ("get_rollout_status", {}),
    ("get_top_events", {"event_type": "bugcheck"}),
]


@pytest.mark.parametrize("name, args", CALLS, ids=[c[0] for c in CALLS])
def test_envelope_and_no_identifiers(t, name, args):
    r = getattr(t, name)(**args)
    assert ENVELOPE <= set(r)
    assert not IDENTIFIER.search(json.dumps(r, default=str))


def test_metric_responses_carry_definition_and_period(t):
    r = t.get_kpi("bsod_rate", week_from="2026-W41", week_to="2026-W41")
    assert r["metric_definition"]["formula"].startswith("1000.0 * bugchecks")
    assert r["period"]["start_date"] == "2026-10-05" and r["period"]["end_date"] == "2026-10-11"
    assert all("devices_reporting" in row for row in r["rows"])


def test_durban_outage_flagged_on_any_durban_slice(t):
    r = t.get_kpi("app_crash_rate", plant="Durban", week_from="2026-W41", week_to="2026-W41")
    codes = {w["code"] for w in r["warnings"]}
    assert "telemetry_outage" in codes and r["data_quality"]["status"] == "critical"


def test_like_for_like_uses_same_devices(t):
    r = t.compare_cohorts("logon_time_p50", "plant", "2026-W36..2026-W38", "2026-W42..2026-W43")
    for row in r["rows"]:
        lfl = row["like_for_like"]
        assert lfl["devices_a"] == lfl["devices_b"]
        assert "all_reporting" in row


def test_rollout_cohort_has_comparison_group(t):
    r = t.compare_cohorts("boot_time_p50", "rollout_cohort", "2026-W36..2026-W37", "2026-W42..2026-W43")
    cohorts = {row["rollout_cohort"] for row in r["rows"]}
    assert any("->" in c for c in cohorts) and any(c.startswith("stayed on") for c in cohorts)


def test_breaches_note_confounders(t):
    r = t.list_threshold_breaches("2026-W41")
    assert r["rows"] and any(row["confounding_warnings"] for row in r["rows"])


def test_mcp_surface_is_read_only_and_reports_reasons():
    from mcp import Client
    from mcp_server.server import server

    async def run():
        async with Client(server) as c:
            listed = (await c.list_tools()).tools
            bad = await c.call_tool("get_kpi", {"metric": "coverage", "plant": "ROS' OR 1=1 --"})
            good = await c.call_tool("get_coverage", {"plant": "DBN"})
            return listed, bad, good

    listed, bad, good = asyncio.run(run())
    assert len(listed) == 14
    assert all(tool.annotations.read_only_hint for tool in listed)
    assert bad.is_error and "Allowed: DBN, GQB, ROS" in bad.content[0].text
    assert not good.is_error and '"rows"' in good.content[0].text
