"""Evidence tier: do the governed tools return the facts a correct answer needs?

These checks run without a model. A failure here is a pipeline or tool problem, not a model
problem, so it is fixed in the pipeline (and noted in the scorecard), never in the eval."""

from mcp_server.tools import FleetTools

MINI = "Elite Mini 800 G9"


def _total(r: dict, key: str | None = None) -> dict:
    rows = r["period_total"]
    return {row.get(key): row for row in rows} if key else rows[0]


def A1_gqeberha_bsod(t: FleetTools) -> list[tuple[str, bool, str]]:
    by_rel = _total(t.get_kpi("bsod_rate", model=MINI, group_by="os_release"), "os_release")
    r25, r24 = by_rel["Win11 25H2"]["value"], by_rel["Win11 24H2"]["value"]
    plants = _total(t.get_kpi("bsod_rate", model=MINI, os_build="25H2", group_by="plant"), "plant")
    gqb = t.get_kpi("bsod_rate", plant="GQB")["rows"]
    codes = t.get_top_events("bugcheck", model=MINI, os_build="25H2", limit=1)["rows"]
    return [
        ("Elite Mini 800 G9 blue screen rate on 25H2 is at least 10x its 24H2 rate",
         r25 >= 10 * r24, f"{r25:.1f} vs {r24:.1f} per 1,000 device-weeks"),
        ("Durban's Elite Minis on 25H2 are at least as bad as Gqeberha's (model, not plant)",
         plants["DBN"]["value"] >= 0.8 * plants["GQB"]["value"],
         f"DBN {plants['DBN']['value']:.1f}, GQB {plants['GQB']['value']:.1f}"),
        ("Gqeberha weekly blue screens rise from about 10 to about 55",
         gqb[0]["bugchecks"] <= 15 and gqb[-1]["bugchecks"] >= 45,
         f"{gqb[0]['bugchecks']} -> {gqb[-1]['bugchecks']}"),
        ("Top bug check code for that slice is 0x116 (video TDR)",
         codes[0]["bugcheck_code"] == "0x00000116", codes[0]["bugcheck_code"]),
    ]


A2_bsod_drivers = A1_gqeberha_bsod


def B1_rosslyn_logon(t: FleetTools):
    row = t.compare_cohorts("logon_time_p50", "none", "2026-W36..2026-W38", "2026-W42..2026-W43",
                            plant="ROS", device_class="SF")["rows"][0]
    lfl, allr = row["like_for_like"], row["all_reporting"]
    return [
        ("All-reporting median logon 'improves' by 10s or more", allr["change"] <= -10,
         f"{allr['period_a']:.1f}s -> {allr['period_b']:.1f}s on {allr['devices_a']} -> {allr['devices_b']} devices"),
        ("Like-for-like change is under 4 seconds", abs(lfl["change"]) < 4,
         f"{lfl['period_a']:.1f}s -> {lfl['period_b']:.1f}s on {lfl['devices_a']} devices"),
        ("Rosslyn shop-floor coverage warning is attached",
         any(w["code"] in ("critical_coverage", "low_coverage", "cohort_mix_shift")
             for w in t.get_coverage(plant="ROS", device_class="SF")["warnings"]), ""),
    ]


def B2_coverage_health(t: FleetTools):
    rows = {r["iso_week"]: r["coverage"] for r in t.get_coverage(plant="ROS", device_class="SF")["rows"]}
    ltsc = {r["iso_week"]: r["coverage"] for r in t.get_kpi("coverage", os_build="Win10 LTSC 21H2")["rows"]
            if r.get("value") is not None for r in [r | {"coverage": r["value"]}]}
    return [
        ("Rosslyn shop-floor coverage falls from ~0.9 to below 0.4",
         rows["2026-W36"] >= 0.85 and rows["2026-W43"] < 0.4, f"{rows['2026-W36']} -> {rows['2026-W43']}"),
        ("Win10 LTSC coverage collapses by W43", ltsc.get("2026-W43", 0) < 0.2,
         f"W36 {ltsc.get('2026-W36')} -> W43 {ltsc.get('2026-W43')}"),
    ] + C1_durban_w41(t)[:1]


def C1_durban_w41(t: FleetTools):
    days = t.get_coverage(plant="DBN", granularity="day", date_from="2026-10-05", date_to="2026-10-09")["rows"]
    kpi = t.get_kpi("startup_count", plant="DBN", week_from="2026-W41", week_to="2026-W41")
    return [
        ("Durban reports zero devices Mon 5 to Fri 9 Oct", all(d["reporting_devices"] == 0 for d in days),
         ", ".join(f"{d['day']}={d['reporting_devices']}" for d in days)),
        ("Durban W41 answers carry the telemetry_outage warning",
         any(w["code"] == "telemetry_outage" for w in kpi["warnings"]), kpi["data_quality"]["status"]),
    ]


def D1_25h2_boot(t: FleetTools, device_class: str | None = None):
    rows = {r["rollout_cohort"]: r["like_for_like"] for r in t.compare_cohorts(
        "boot_time_p50", "rollout_cohort", "2026-W36..2026-W37", "2026-W42..2026-W43",
        device_class=device_class)["rows"]}
    up, stay = rows["Win11 24H2 -> Win11 25H2"], rows["stayed on Win11 24H2"]
    return [
        ("Upgraded devices' median boot falls 8-15%", -0.15 <= up["pct_change"] <= -0.08,
         f"{up['period_a']:.1f}s -> {up['period_b']:.1f}s ({up['pct_change']:+.1%}, {up['devices_a']} devices)"),
        ("Comparison group (stayed on 24H2) is flat within 2%", abs(stay["pct_change"]) <= 0.02,
         f"{stay['period_a']:.1f}s -> {stay['period_b']:.1f}s ({stay['devices_a']} devices)"),
    ]


def D2_office_naive(t: FleetTools):
    return D1_25h2_boot(t, device_class="OF")


def E1_crash_spike(t: FleetTools):
    w41 = t.get_top_events("app_crash", week_from="2026-W41", week_to="2026-W41", limit=1)["rows"][0]
    later = [r for r in t.get_top_events("app_crash", week_from="2026-W42", week_to="2026-W43", limit=10)["rows"]
             if r["app_name"] == "KestrelMES.exe"]
    plants = {p: t.get_top_events("app_crash", week_from="2026-W41", week_to="2026-W41", plant=p, limit=1)["rows"][0]
              for p in ("ROS", "GQB", "DBN")}
    return [
        ("W41 top crashing app is KestrelMES 4.2.0", (w41["app_name"], w41["app_version"]) == ("KestrelMES.exe", "4.2.0"),
         f"{w41['app_name']} {w41['app_version']}: {w41['events']} crashes"),
        ("4.2.0 does not appear after W41; 4.2.1 does", bool(later) and all(r["app_version"] == "4.2.1" for r in later),
         ", ".join(f"{r['app_version']}={r['events']}" for r in later)),
        ("Spike is the top crash at every plant in W41", all(r["app_name"] == "KestrelMES.exe" for r in plants.values()),
         ", ".join(f"{p}:{r['events']}" for p, r in plants.items())),
    ]


def E2_rosslyn_mes(t: FleetTools):
    rows = {r["iso_week"]: r for r in t.get_kpi("app_crash_rate", plant="ROS", device_class="SF",
                                                week_from="2026-W40", week_to="2026-W43")["rows"]}
    return [
        ("Rosslyn shop-floor reporting devices fall by more than half W40 -> W43",
         rows["2026-W43"]["devices_reporting"] < 0.5 * rows["2026-W40"]["devices_reporting"],
         f"{rows['2026-W40']['devices_reporting']} -> {rows['2026-W43']['devices_reporting']}"),
        ("W43 crash rate is back near W40 (not below it by more than 25%)",
         rows["2026-W43"]["value"] >= 0.75 * rows["2026-W40"]["value"],
         f"W40 {rows['2026-W40']['value']:.0f}, W41 {rows['2026-W41']['value']:.0f}, W43 {rows['2026-W43']['value']:.0f}"),
    ]


def R1_tracker_wave2(t: FleetTools):
    totals = {r["reconciliation_status"]: r["devices"] for r in
              t.get_rollout_status(wave="Wave 2")["totals_by_reconciliation_status"]}
    return [
        ("Most Wave 2 devices are confirmed by telemetry", totals.get("confirmed", 0) > 5000, str(totals.get("confirmed"))),
        ("Some Done claims are contradicted or unverifiable",
         totals.get("claimed_not_observed", 0) + totals.get("unverifiable_stale_telemetry", 0) > 0,
         f"claimed_not_observed={totals.get('claimed_not_observed', 0)}, "
         f"unverifiable={totals.get('unverifiable_stale_telemetry', 0)}"),
    ]


def G1_hostnames(t: FleetTools):
    names = {n for n in dir(t) if not n.startswith("_") and callable(getattr(t, n))}
    tools = {"list_allowed_values", "get_metric_definition", "get_kpi", "compare_cohorts", "get_coverage",
             "get_data_quality_status", "list_threshold_breaches", "get_rollout_status", "get_top_events"}
    return [("No tool returns device-level rows (tool surface is the 9 aggregate tools)", tools <= names, "")]


G2_user = G1_hostnames


def W1_week_summary(t: FleetTools):
    return C1_durban_w41(t) + E1_crash_spike(t)[:1]


# ------------------------------------------------------------------ business layer (Phase 6)

def F1_durban_hidden_mes(t: FleetTools):
    d = t.get_business_impact("2026-W41", "2026-W41", plant="DBN")["downtime_total"]
    tel = t.get_top_events("app_crash", week_from="2026-W41", week_to="2026-W41", plant="DBN", limit=1)["rows"]
    return [("Durban lost 300+ line-stop minutes to MES client faults in W41", d["it_app_minutes"] > 300,
             f"{d['it_app_minutes']} min"),
            ("Telemetry under-counts Durban MES crashes in W41 (outage)",
             not tel or tel[0].get("events", 0) < 500, str(tel[0].get("events") if tel else 0))] + C1_durban_w41(t)[:1]


def F2_mes_cost(t: FleetTools):
    r = t.get_business_impact("2026-W41", "2026-W41")
    top = r["cost_by_driver"][0]
    return [("W41 total cost is a range in the low millions (ZAR)",
             1e6 < r["cost_total_zar"]["low"] < r["cost_total_zar"]["high"] < 1e7,
             f"R{r['cost_total_zar']['low']:,.0f} - R{r['cost_total_zar']['high']:,.0f}"),
            ("Top cost driver is MES client line stops", "IT-APP" in top["driver"],
             f"{top['driver']}: R{top['cost_low_zar']:,.0f} - R{top['cost_high_zar']:,.0f}"),
            ("Assumptions are flagged illustrative", r["cost_assumptions"]["illustrative"] is True, "")]


def G1_bsod_change(t: FleetTools):
    s = {c["change_id"]: c for c in t.get_change_correlation(include_no_evidence=True)["rows"]}
    w2 = t.get_change_correlation(change_id="CHG0041021", metric="bsod_rate")["rows"][0]
    return [("Wave 2 25H2 ring: blue screens up in scope, not in comparison",
             w2["evidence"] in ("moderate", "strong") and w2["rel_change_scope"] > 0.3,
             f"{w2['evidence']}: {w2['rel_change_scope']:+.0%} vs {w2['rel_change_comparison']:+.0%}"),
            ("Driver-update change carries the 0x116 shift (weak, with Wave 2 competing)",
             s["CHG0041007"]["evidence"] == "weak" and "CHG0041021" in (s["CHG0041007"]["competing_changes"] or ""),
             str(s["CHG0041007"]["mechanism"]))] + A1_gqeberha_bsod(t)[3:4]


def G2_mini_pause(t: FleetTools):
    r = {x["action_id"]: x for x in t.get_remediation_status()["rows"]}["RA-001"]
    n = int(r["implementation_evidence"].split()[0])
    return [("Pause verdict is claimed_not_implemented", r["verdict"] == "claimed_not_implemented", r["verdict"]),
            ("Hundreds of Minis first reported 25H2 after 14 Oct", n > 300, r["implementation_evidence"])]


def F3_hotfix(t: FleetTools):
    r = {x["action_id"]: x for x in t.get_remediation_status()["rows"]}["RA-003"]
    return [("Hotfix verdict is effective", r["verdict"] == "effective", r["verdict"]),
            ("MES crash rate falls 70%+ after the hotfix", r["rel_change"] <= -0.7,
             f"{r['before']} -> {r['after']} ({r['rel_change']:+.0%})")]


def H1_outage_detection(t: FleetTools):
    rows = t.get_timeline("2026-W41", "2026-W41", plant="DBN")["rows"]
    kinds = {(x["kind"], x["ref"][:3]): x for x in rows}
    det = next(x for x in rows if x["kind"] == "detection")
    proxy = t.get_change_correlation(change_id="CHG0041030", metric="coverage")["rows"][0]
    return [("Detection ticket opened Thu 8 Oct", det["event_at"].startswith("2026-10-08"), det["event_at"]),
            ("Proxy change shows strong coverage drop for Durban", proxy["evidence"] == "strong",
             f"{proxy['rel_change_scope']:+.0%}"),
            ("Allow-list remediation is effective",
             {x["action_id"]: x["verdict"] for x in t.get_remediation_status()["rows"]}["RA-005"] == "effective", "")]


def I1_ltsc_tickets(t: FleetTools):
    v = {x["action_id"]: x["verdict"] for x in t.get_remediation_status()["rows"]}
    return B2_coverage_health(t)[1:2] + [("LTSC investigation is still open", v["RA-007"] == "open", v["RA-007"])]


def J1_defender(t: FleetTools):
    d = t.get_change_correlation(change_id="CHG0041033", metric="app_crash_rate")["rows"][0]
    return [("Defender change: no comparison group, weak at most", not d["has_comparison"] and d["evidence"] == "weak",
             d["evidence"]),
            ("Its crash shift is explained by CHG0041031", "CHG0041031" in (d["mechanism_change"] or ""),
             str(d["mechanism"]))]


def K1_bsod_desk(t: FleetTools):
    r = t.get_ticket_metrics(category="Display / blue screen", group_by="plant")["period_total"][0]
    return [("Blue screen tickets: median resolution over a day", r["mttr_p50_hours"] > 24, f"{r['mttr_p50_hours']} h"),
            ("Blue screen tickets breach SLA more than a third of the time", r["sla_breach_rate"] > 0.33,
             f"{r['sla_breach_rate']:.0%}")]
