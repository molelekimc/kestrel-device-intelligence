"""The business sources join to the same devices as the device sources (the contract the
business pipeline relies on). Reads raw files only."""

import csv

import pytest

from pipeline import config
from pipeline.standardise import std_hostname

RAW = config.RAW
FILES = {
    "itsm/servicedesk_incidents_20261026.csv": ["number", "opened_at", "first_response_at", "resolved_at",
                                                "closed_at", "state", "priority", "category", "short_description",
                                                "caller_id", "location", "cmdb_ci", "assignment_group",
                                                "parent_incident", "problem_id", "caused_by_change", "close_code",
                                                "reopen_count", "sys_updated_on"],
    "change/change_requests_20261026.csv": ["number", "type", "category", "short_description", "assignment_target",
                                            "risk", "planned_start", "planned_end", "actual_start", "actual_end",
                                            "state", "close_code", "problem_id", "requested_by"],
    "intune/group_membership_20261026.csv": ["group_name", "member_name", "member_type"],
    "mes/production_lines.csv": ["line_id", "mes_plant", "site_name", "product_family", "standard_units_per_hour",
                                 "shift_pattern"],
    "mes/line_terminals.csv": ["line_id", "station", "terminal_name", "line_critical"],
    "mes/line_downtime_events.csv": ["event_id", "mes_plant", "line", "station", "start_local", "end_local",
                                     "duration_min", "reason_code", "shift", "operator_comment"],
    "remediation/remediation_register.csv": ["action_id", "problem_id", "title", "linked_change", "owner", "target",
                                             "status", "date_raised", "due_date", "date_actioned", "verified",
                                             "expected_benefit", "comments"],
}


def rows(rel):
    with open(RAW / rel, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def intune_devices():
    return {std_hostname(r["Device name"]) for r in rows("intune/kestrel_intune_devices_20261026.csv")}


@pytest.mark.parametrize("rel, header", FILES.items(), ids=list(FILES))
def test_file_has_expected_header(rel, header):
    with open(RAW / rel, encoding="utf-8-sig") as f:
        assert next(csv.reader(f)) == header


def test_ticket_cis_resolve_to_intune_devices(intune_devices):
    cis = [std_hostname(r["cmdb_ci"]) for r in rows("itsm/servicedesk_incidents_20261026.csv") if r["cmdb_ci"].strip()]
    assert cis and sum(c in intune_devices for c in cis) / len(cis) > 0.99


def test_terminals_are_intune_shop_floor_devices(intune_devices):
    names = [std_hostname(r["terminal_name"]) for r in rows("mes/line_terminals.csv")]
    assert all(n in intune_devices and n[4:6] == "SF" for n in names)


def test_downtime_lines_exist():
    lines = {r["line_id"] for r in rows("mes/production_lines.csv")}
    stops = rows("mes/line_downtime_events.csv")
    exact = sum(r["line"] in lines for r in stops) / len(stops)
    assert 0.95 < exact < 1.0          # a few lines are written 'BIW L01' style on purpose


def test_change_targets_are_known_groups():
    groups = {r["group_name"] for r in rows("intune/group_membership_20261026.csv")}
    norm = lambda g: g.strip().upper().replace(" ", "-")
    targets = {norm(r["assignment_target"]) for r in rows("change/change_requests_20261026.csv")}
    assert targets <= {norm(g) for g in groups}
