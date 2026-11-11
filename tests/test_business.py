"""Business layer (Phase 6): cleaning rules on the miniature sources in conftest.py, cost
arithmetic, evidence and verdict rules, guardrails, and storyline outputs on the real warehouse."""

import json
import re

import pytest

from mcp_server.guardrails import IdentifierLeak, ToolInputError
from mcp_server.tools import FleetTools
from pipeline import config
from pipeline import standardise as s
from pipeline.gold_business import evidence_label, relevant_metrics, remediation_verdict
from pipeline.metrics import definitions


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


# ----------------------------------------------------------------- pure functions

@pytest.mark.parametrize("raw, expected", [
    ("1 - Critical", 1), ("P2", 2), ("High", 2), ("3 - Moderate", 3), ("Low", 4), ("urgent", None)])
def test_std_priority(raw, expected):
    assert s.std_priority(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("BSOD", "Display / blue screen"), ("KestrelMES", "MES"), ("HW", "Hardware"), ("Slow PC", "Performance"),
    ("Platform - Telemetry", "Monitoring"), ("Facilities", None)])
def test_std_ticket_category(raw, expected):
    assert s.std_ticket_category(raw) == expected


@pytest.mark.parametrize("plant, raw, expected", [
    ("P01", "P01-BIW-L01", "P01-BIW-L01"), ("P01", "BIW L01", "P01-BIW-L01"), ("P02", "axle l03", "P02-AXLE-L03"),
    ("P01", "line one", None)])
def test_std_line_id(plant, raw, expected):
    assert s.std_line_id(plant, raw) == expected


@pytest.mark.parametrize("code, comment, expected", [
    ("IT-APP", "", ("IT-APP", False)),
    ("OTHER", "screen froze", ("IT-TERM", True)),
    ("OTHER", "terminal rebooted itself", ("IT-TERM", True)),
    ("OTHER", "MES closed on its own", ("IT-APP", True)),
    ("OTHER", "screen message stuck", ("IT-TERM", True)),       # 'message' is not MES
    ("OTHER", "waiting for forklift", (None, False)),
    ("MAT", "no parts", (None, False)),
])
def test_downtime_it_type(code, comment, expected):
    assert s.downtime_it_type(code, comment) == expected


def test_dates_and_groups():
    assert str(s.parse_local_datetime("05/10/2026 07:40")) == "2026-10-05 07:40:00"
    assert str(s.parse_day("14 Oct 2026")) == "2026-10-14"
    assert s.std_group(" grp mes users ") == "GRP-MES-USERS"
    assert s.std_remediation_status("Completed") == "Done"


RULES = definitions()["business"]["change_impact"]


@pytest.mark.parametrize("rel_s, rel_c, n, events, comp, expected", [
    (1.8, 0.03, 8000, 9000, True, "strong"),          # big, specific
    (0.30, 0.02, 8000, 9000, True, "moderate"),
    (1.2, 1.0, 8000, 9000, True, "weak"),              # comparison moved as much
    (0.6, None, 18000, 9000, False, "weak"),           # no comparison group
    (0.1, 0.0, 8000, 9000, True, "none"),
    (2.0, 0.0, 12, 9000, True, "insufficient_data"),   # too few devices
    (2.0, 0.0, 8000, 5, True, "insufficient_data"),    # too few events
])
def test_evidence_label(rel_s, rel_c, n, events, comp, expected):
    assert evidence_label(rel_s, rel_c, n, events, comp, RULES) == expected


def test_relevant_metrics_by_type_and_keyword():
    assert relevant_metrics("Network", "Durban proxy", RULES) == ["coverage", "tickets_per_1k"]
    assert relevant_metrics("Intune - App deployment", "Deploy reliability agent 2.4", RULES) == ["coverage"]
    assert "bsod_rate" in relevant_metrics("Something new", "", RULES)


REM = definitions()["business"]["remediation"]


@pytest.mark.parametrize("rel, expected_dir, rel_c, comp, verdict", [
    (-0.85, "decrease", None, True, "effective"),
    (1.5, "increase", -0.1, True, "effective"),
    (0.05, "decrease", 0.1, True, "no_detectable_effect"),
    (-0.5, "decrease", -0.45, True, "inconclusive"),         # comparison improved as much
    (0.6, "decrease", None, False, "worse"),
    (None, "decrease", None, False, "not_yet_measurable"),
])
def test_remediation_verdict(rel, expected_dir, rel_c, comp, verdict):
    assert remediation_verdict(rel, expected_dir, rel_c, comp, REM) == verdict


# ----------------------------------------------------------------- Silver on the miniature sources

def test_ticket_cleaning(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT number, device_key, plant_code, priority, category, is_child, is_major, mttr_min, sla_breached,
               time_invalid, assignment_group, reopen_count FROM silver_ticket""").fetchall()}
    assert set(rows) == {"INC1", "INC2", "INC3", "INC4"}
    assert rows["INC1"][:5] == ("ROS-OF-00001", "ROS", 2, "Display / blue screen", False)
    assert rows["INC1"][8:] == (False, "EUC Engineering", 1)           # later export row kept
    assert rows["INC2"][1:4] == ("GQB", 3, "Performance") and rows["INC2"][6] is None and rows["INC2"][8]
    assert rows["INC3"][4:8] == (False, True, 480.0, True)              # major, 8 h > 4 h SLA
    assert rows["INC4"][0] == "GQB-SF-00002" and rows["INC4"][4] is True


def test_ticket_accounting_and_text_isolated(mini_db):
    bronze = one(mini_db, "SELECT count(*) FROM bronze_itsm_incidents")
    kept = one(mini_db, "SELECT count(*) FROM silver_ticket")
    held = one(mini_db, "SELECT count(*) FROM quarantine WHERE source = 'itsm' AND NOT retained")
    assert bronze == kept + held == 5
    cols = [r[0] for r in mini_db.execute("DESCRIBE silver_ticket").fetchall()]
    assert "caller_id" not in cols and "short_description" not in cols
    assert one(mini_db, "SELECT count(*) FROM silver_ticket_text") == 4


def test_change_scope_and_groups(mini_db):
    assert dict(mini_db.execute("SELECT change_id, implemented FROM silver_change").fetchall()) == \
        {"CHG1": True, "CHG2": False}
    assert one(mini_db, "SELECT target_group FROM silver_change WHERE change_id = 'CHG1'") == "GRP-MES-USERS"
    scope = {r[0] for r in mini_db.execute("SELECT device_key FROM silver_change_scope WHERE change_id = 'CHG1'").fetchall()}
    assert scope == {"GQB-SF-00002", "ROS-OF-00001"}
    assert one(mini_db, "SELECT count(*) FROM quarantine WHERE reason = 'member_not_in_intune'") == 1


def test_downtime_cleaning(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT event_id, line_id, plant_code, device_key, minutes, is_it, it_type, it_reclassified
        FROM silver_downtime""").fetchall()}
    assert set(rows) == {"DT1", "DT2", "DT3", "DT4"}                     # DT1D duplicate removed
    assert rows["DT1"] == ("P02-AXLE-L01", "GQB", "GQB-SF-00002", 10.0, True, "IT-APP", False)
    assert rows["DT2"][3:] == (30.0, True, "IT-TERM", True)             # OTHER 'screen froze'; typed in hours
    assert rows["DT3"][4] is False and rows["DT4"][0] == "P02-AXLE-L01"  # 'AXLE L01' rebuilt
    log = dict(mini_db.execute("SELECT check_name, rows_affected FROM dq_log").fetchall())
    assert log["mes_duration_keyed_in_hours"] == 1 and log["mes_stop_duplicate"] == 1
    assert log["mes_other_reclassified_it"] == 1


def test_production_cost_arithmetic(mini_db):
    rows = {r[0]: r[1:] for r in mini_db.execute("""
        SELECT driver, quantity, cost_low, cost_high FROM gold_cost_weekly
        WHERE cost_type = 'production_downtime'""").fetchall()}
    # GQB margin R700-R1,200 per line-minute; 50% / 20% of output recovered.
    assert rows["MES client faults (IT-APP)"] == (10.0, pytest.approx(10 * 700 * 0.5), pytest.approx(10 * 1200 * 0.8))
    assert rows["Station PC faults (IT-TERM)"][1] == pytest.approx(30 * 700 * 0.5)


def test_support_cost_child_share(mini_db):
    low = one(mini_db, """SELECT sum(cost_low) FROM gold_cost_weekly
                          WHERE cost_type = 'it_support' AND driver = 'Incidents: MES'""")
    assert low == pytest.approx(3500 + 900 * 0.3)    # P1 parent + P2 child at 30%


def test_remediation_claimed_but_change_cancelled(mini_db):
    verdicts = dict(mini_db.execute("SELECT action_id, verdict FROM gold_remediation_effect").fetchall())
    assert verdicts == {"RA-900": "claimed_not_implemented", "RA-901": "open"}


# ----------------------------------------------------------------- guardrails on the miniature sources

@pytest.fixture(scope="module")
def mini_tools(mini_db_path):
    return FleetTools(mini_db_path, caller="test")


def test_small_ticket_groups_suppressed(mini_tools):
    r = mini_tools.get_ticket_metrics(group_by="category")
    assert r["rows"] and all(x.get("suppressed") for x in r["rows"])
    assert all("mttr_p50_hours" not in x for x in r["rows"])


def test_people_never_returned(mini_tools):
    assert "Test Caller" in mini_tools._out.people
    with pytest.raises(IdentifierLeak, match="person"):
        mini_tools._out.check({"rows": [{"note": "called by Test Caller"}]})
    for name, args in (("get_business_impact", {}), ("get_ticket_metrics", {"group_by": "plant"}),
                       ("get_change_correlation", {"include_no_evidence": True}), ("get_remediation_status", {}),
                       ("get_timeline", {}), ("get_floor_status", {})):
        text = json.dumps(getattr(mini_tools, name)(**args), default=str)
        assert "Test Caller" not in text and "Other Person" not in text and "KP" not in re.findall(r"\b[A-Z]{2}\b", text)


def test_business_inputs_validated(mini_tools):
    with pytest.raises(ToolInputError):
        mini_tools.get_ticket_metrics(category="'; DROP TABLE silver_ticket; --")
    with pytest.raises(ToolInputError):
        mini_tools.get_ticket_metrics(priority="urgent")
    with pytest.raises(ToolInputError):
        mini_tools.get_change_correlation(change_id="CHG9999999")


# ----------------------------------------------------------------- storylines on the real warehouse

real = pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel.duckdb not built")


@pytest.fixture(scope="module")
def t():
    return FleetTools(caller="test")


@real
def test_mes_regression_dominates_w41(t):
    r = t.get_business_impact("2026-W41", "2026-W41")
    assert r["downtime_total"]["it_downtime_share"] > 0.25
    top = r["cost_by_driver"][0]
    assert top["cost_type"] == "production_downtime" and "IT-APP" in top["driver"]
    durban = t.get_business_impact("2026-W41", "2026-W41", plant="DBN")
    assert durban["downtime_total"]["it_app_minutes"] > 300          # visible in MES despite the outage


@real
def test_change_evidence_storylines(t):
    s = {r["change_id"]: r for r in t.get_change_correlation(include_no_evidence=True)["rows"]}
    assert s["CHG0041031"]["evidence"] == "strong" and s["CHG0041031"]["mechanism_change"] == "CHG0041031"
    assert s["CHG0041033"]["evidence"] == "weak" and "CHG0041031" in s["CHG0041033"]["mechanism_change"]
    assert s["CHG0041030"]["evidence"] == "strong" and s["CHG0041030"]["direction"] == "decrease"
    assert s["CHG0041038"]["evidence"] == "strong" and s["CHG0041038"]["direction"] == "increase"
    assert s["CHG0041039"]["direction"] == "decrease"


@real
def test_remediation_storylines(t):
    v = {r["action_id"]: r["verdict"] for r in t.get_remediation_status()["rows"]}
    assert v == {"RA-001": "claimed_not_implemented", "RA-002": "not_yet_measurable", "RA-003": "effective",
                 "RA-004": "not_measurable_process", "RA-005": "effective", "RA-006": "open", "RA-007": "open",
                 "RA-008": "no_detectable_effect"}


@real
def test_no_people_in_any_business_output(t):
    for name, args in (("get_business_impact", {}), ("get_ticket_metrics", {"group_by": "category"}),
                       ("get_change_correlation", {"include_no_evidence": True}), ("get_remediation_status", {}),
                       ("get_timeline", {}), ("get_floor_status", {})):
        text = json.dumps(getattr(t, name)(**args), default=str)
        assert not any(p in text for p in t._out.people)


# ----------------------------------------------------------------- control room (Phase 8)

def test_floor_status_small_areas_hidden(mini_tools):
    r = mini_tools.get_floor_status("2026-W41")
    assert {"period", "rows", "devices_by_plant", "devices_total", "fixes", "incidents", "bands",
            "warnings", "data_freshness"} <= set(r)
    small = [a for a in r["rows"] if a.get("suppressed")]
    assert small and all(a["band"] is None and "it_minutes" not in a for a in small)
    assert all(a["area"] and a["plant_name"] for a in r["rows"])
    assert "device_key" not in json.dumps(r) and "line_id" not in json.dumps(r)


def test_floor_status_inputs_validated(mini_tools):
    with pytest.raises(ToolInputError):
        mini_tools.get_floor_status(plant="Atlantis")
    with pytest.raises(ToolInputError):
        mini_tools.get_floor_status(week="2026-W99")


@pytest.mark.parametrize("share, band", [(None, None), (0.05, "normal"), (0.12, "watch"), (0.199, "watch"),
                                         (0.20, "hurting"), (0.4, "hurting")])
def test_floor_bands(mini_tools, share, band):
    assert mini_tools._band(share) == band


@real
def test_floor_w41_story(t):
    r = t.get_floor_status("2026-W41")
    assert {a["band"] for a in r["rows"]} == {"hurting"}                      # the MES incident hit every area
    dbn = next(d for d in r["devices_by_plant"] if d["plant"] == "DBN")
    assert dbn["silent"] > dbn["in_use"] / 2                                  # outage shows as silent, not healthy
    assert all(d["healthy"] + d["having_problems"] + d["silent"] == d["in_use"] for d in r["devices_by_plant"])
    quiet = t.get_floor_status("2026-W38")
    assert "normal" in {a["band"] for a in quiet["rows"]}
