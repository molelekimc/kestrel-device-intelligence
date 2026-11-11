"""Weekly executive report: one self-contained HTML page per ISO week.

    python -m reporting.build --week 2026-W41      ->  reports/2026-W41.html

Built only from the governed FleetTools (caller 'report'), so the page shows nothing the
assistant could not: aggregates, suppression, no device or person identifiers, and the
data quality warnings that qualify each number. Charts are inline SVG; no scripts or
external assets, so the file works offline and can be attached to an email."""

import argparse
import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from automation import drafts
from mcp_server.guardrails import ToolInputError
from mcp_server.tools import FleetTools
from pipeline.config import ROOT

REPORTS = ROOT / "reports"
E = html.escape

# Reference palette (dataviz skill): categorical slots 1-3 validated all-pairs in both modes.
SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"]
STATUS = {  # verdict / evidence -> (status role, icon, label)
    "effective": ("good", "✓", "Effective"),
    "no_detectable_effect": ("warning", "–", "No detectable effect"),
    "claimed_not_implemented": ("critical", "✕", "Claimed, not implemented"),
    "not_yet_measurable": ("neutral", "…", "Not yet measurable"),
    "not_measurable_process": ("neutral", "·", "Process change"),
    "open": ("serious", "!", "Open"),
    "inconclusive": ("warning", "?", "Inconclusive"),
    "worse": ("critical", "✕", "Worse after action"),
    # Evidence strength is not a severity, so it never wears a status colour.
    "strong": ("neutral", "●", "Strong evidence"),
    "moderate": ("neutral", "◐", "Moderate evidence"),
    "weak": ("neutral", "○", "Weak evidence"),
    "critical": ("critical", "✕", "Critical"),
    "warning": ("warning", "!", "Warning"),
    "ok": ("good", "✓", "OK"),
    "ok_with_notes": ("good", "✓", "OK, with notes"),
}


# --------------------------------------------------------------------------- formatting

def _shown(v):
    """Suppressed values arrive as text such as '<10' and are shown as they are."""
    return v if isinstance(v, str) else None


def _numeric(v):
    """Chart input: suppressed or missing values are not plotted."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def zar(v):
    if v is None:
        return "–"
    if _shown(v):
        return v
    for div, suf in ((1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"R{v / div:.1f}{suf}"
    return f"R{v:,.0f}"


def num(v, nd=0):
    return "–" if v is None else (_shown(v) or f"{v:,.{nd}f}")


def pct(v, nd=0):
    return "–" if v is None else (_shown(v) or f"{100 * v:.{nd}f}%")


def signed_pct(v):
    return "–" if v is None else f"{'+' if v >= 0 else '−'}{abs(100 * v):.0f}%"


def badge(key):
    role, icon, label = STATUS.get(key, ("neutral", "·", key.replace("_", " ")))
    return f'<span class="badge {role}"><span aria-hidden="true">{icon}</span> {E(label)}</span>'


def short_week(w):
    return w.split("-")[1]


def nice_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %d %b %Y")


def week_range(r):
    """'2026-W39..2026-W39' -> 'W39'; '2026-W40..2026-W41' -> 'W40–W41'."""
    a, _, b = (r or "").partition("..")
    if not a:
        return ""
    return short_week(a) if a == b or not b else f"{short_week(a)}–{short_week(b)}"


COST_TYPE = {"production_downtime": "Production downtime", "workforce_productivity": "Workforce productivity",
             "it_support": "IT support"}
METRIC_NAME = {"app_crash_rate": "app crash rate", "bsod_rate": "blue screen rate", "coverage": "telemetry coverage",
               "tickets_per_1k": "incidents per device", "it_downtime_per_100": "IT line-stop time",
               "boot_time_p50": "median boot time"}


# --------------------------------------------------------------------------- charts (inline SVG)

def columns(labels, series, highlight=None, unit="", height=180, stacked=True):
    """Stacked (or single) columns: series = [(name, values)]. One axis, 2px surface gap,
    4px rounded data end, native tooltips."""
    w, pad_l, pad_b, pad_t = 640, 44, 24, 10
    series = [(name, [_numeric(v) for v in vals]) for name, vals in series]
    n = len(labels)
    band = (w - pad_l) / n
    bar = min(24, band * 0.5)
    totals = [sum(v[i] or 0 for _, v in series) for i in range(n)]
    top = max(totals + [1])
    step = 10 ** max(0, len(str(int(top))) - 1)
    top = (int(top / step) + 1) * step
    y = lambda v: pad_t + (height - pad_t - pad_b) * (1 - v / top)
    out = [f'<svg viewBox="0 0 {w} {height}" role="img" class="chart">']
    for t in (0, top / 2, top):
        out.append(f'<line x1="{pad_l}" x2="{w}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                   f'<text x="{pad_l - 6}" y="{y(t) + 4:.1f}" class="tick" text-anchor="end">{num(t)}</text>')
    for i, lab in enumerate(labels):
        x = pad_l + band * i + (band - bar) / 2
        base = 0
        for k, (name, vals) in enumerate(series):
            v = vals[i] or 0
            if v <= 0:
                continue
            y0, y1 = y(base), y(base + v)
            hgt = max(0, y0 - y1 - (2 if base > 0 else 0))
            is_top = k == len(series) - 1 or all((s[1][i] or 0) <= 0 for s in series[k + 1:])
            r = 4 if is_top and hgt > 4 else 0
            path = (f"M{x:.1f},{y0 - (2 if base > 0 else 0):.1f} V{y1 + r:.1f} Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f} "
                    f"H{x + bar - r:.1f} Q{x + bar:.1f},{y1:.1f} {x + bar:.1f},{y1 + r:.1f} "
                    f"V{y0 - (2 if base > 0 else 0):.1f} Z")
            dim = "" if highlight in (None, lab) else ' opacity="0.45"'
            out.append(f'<path d="{path}" fill="{SERIES[k]}"{dim}><title>{E(lab)} · {E(name)}: '
                       f'{num(v)}{unit}</title></path>')
            base += v
        out.append(f'<text x="{x + bar / 2:.1f}" y="{height - 6}" class="tick" text-anchor="middle">'
                   f'{E(short_week(lab))}</text>')
        if lab == highlight:
            out.append(f'<text x="{x + bar / 2:.1f}" y="{y(totals[i]) - 6:.1f}" class="value" '
                       f'text-anchor="middle">{num(totals[i])}</text>')
    out.append("</svg>")
    return "".join(out)


def lines(labels, series, height=180, fmt=pct, ymin=0.0, ymax=1.0, shade=None):
    """2px lines with end labels; shade = set of labels to mark (e.g. outage weeks)."""
    w, pad_l, pad_r, pad_b, pad_t = 640, 44, 90, 24, 10
    series = [(name, [_numeric(v) for v in vals]) for name, vals in series]
    n = len(labels)
    xs = [pad_l + (w - pad_l - pad_r) * i / max(1, n - 1) for i in range(n)]
    y = lambda v: pad_t + (height - pad_t - pad_b) * (1 - (v - ymin) / (ymax - ymin))
    out = [f'<svg viewBox="0 0 {w} {height}" role="img" class="chart">']
    for i, lab in enumerate(labels):
        if shade and lab in shade:
            half = (xs[1] - xs[0]) / 2 if n > 1 else 10
            out.append(f'<rect x="{xs[i] - half:.1f}" y="{pad_t}" width="{2 * half:.1f}" '
                       f'height="{height - pad_t - pad_b}" class="shade"><title>{E(shade[lab])}</title></rect>')
    for t in (ymin, (ymin + ymax) / 2, ymax):
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                   f'<text x="{pad_l - 6}" y="{y(t) + 4:.1f}" class="tick" text-anchor="end">{fmt(t)}</text>')
    for i, lab in enumerate(labels):
        out.append(f'<text x="{xs[i]:.1f}" y="{height - 6}" class="tick" text-anchor="middle">{E(short_week(lab))}</text>')
    for k, (name, vals) in enumerate(series):
        pts = [(xs[i], y(v)) for i, v in enumerate(vals) if v is not None]
        if not pts:
            continue
        d = " ".join(f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{SERIES[k]}" stroke-width="2" stroke-linejoin="round" '
                   f'stroke-linecap="round"/>')
        for i, v in enumerate(vals):
            if v is not None:
                out.append(f'<circle cx="{xs[i]:.1f}" cy="{y(v):.1f}" r="4" fill="{SERIES[k]}" class="dot">'
                           f'<title>{E(labels[i])} · {E(name)}: {fmt(v)}</title></circle>')
        lx, ly = pts[-1]
        last = next(v for v in reversed(vals) if v is not None)
        out.append(f'<text x="{lx + 8:.1f}" y="{ly + 4:.1f}" class="value">{E(name)} {fmt(last)}</text>')
    out.append("</svg>")
    return "".join(out)


def legend(names):
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{SERIES[k]}"></i>{E(n)}</span>' for k, n in enumerate(names)) + "</div>"


def table(headers, rows, caption=None):
    cap = f"<caption>{E(caption)}</caption>" if caption else ""
    head = "".join(f"<th>{E(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def data_view(headers, rows):
    return f"<details><summary>Table view</summary>{table(headers, [[E(str(c)) for c in r] for r in rows])}</details>"


def md_to_html(md: str) -> str:
    """Just enough Markdown for an approved briefing: headings, bullets, paragraphs, bold."""
    out, in_list = [], False
    for line in md.splitlines():
        t = E(line.strip())
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        if line.startswith("#"):
            if in_list:
                out.append("</ul>"); in_list = False
            level = min(4, len(line) - len(line.lstrip("#")) + 2)
            out.append(f"<h{level}>{t.lstrip('#').strip()}</h{level}>")
        elif line.lstrip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{t[2:]}</li>")
        elif t:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{t}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# --------------------------------------------------------------------------- data (governed tools only)

def gather(t: FleetTools, week: str) -> dict:
    weeks = t.v.weeks[: t.v.weeks.index(week) + 1]
    prev = weeks[-2] if len(weeks) > 1 else None
    first = weeks[0]
    return {
        "week": week, "prev": prev, "weeks": weeks,
        "period": t._period(week, week),
        "impact_week": t.get_business_impact(week, week),
        "impact_prev": t.get_business_impact(prev, prev) if prev else None,
        "impact_trend": t.get_business_impact(first, week),
        "tickets": t.get_ticket_metrics(first, week, group_by="week"),
        "tickets_by_category": t.get_ticket_metrics(week, week, group_by="category"),
        "coverage": t.get_coverage(date_from=None, date_to=None, granularity="week"),
        "kpi": t.get_kpi("bsod_rate", week_from=first, week_to=week),
        "crash": t.get_kpi("app_crash_rate", week_from=first, week_to=week),
        "dq": t.get_data_quality_status(week),
        "changes": t.get_change_correlation(prev or week, week, include_no_evidence=False),
        "remediation": t.get_remediation_status(),
        "timeline": t.get_timeline(week, week),
        "data_end": t._period(t.v.weeks[-1], t.v.weeks[-1])["end_date"],
    }


def approved_briefing(week: str) -> str | None:
    for p in sorted(drafts.approved_dir().glob(f"{week}_*.md"), reverse=True):
        meta, body, _ = drafts.load(p)
        if meta.get("status") == "approved":
            return f"<p class='meta'>Approved by {E(str(meta.get('approved_by', 'reviewer')))} · " \
                   f"drafted with {E(str(meta.get('model', '')))}</p>" + md_to_html(body)
    return None


# --------------------------------------------------------------------------- page

def render(d: dict, briefing_html: str | None) -> str:
    w, prev = d["week"], d["prev"]
    iw, ip = d["impact_week"], d["impact_prev"]
    dt, dtp = iw["downtime_total"], (ip or {}).get("downtime_total", {})
    cost = iw["cost_total_zar"]
    status = d["dq"]["data_quality"]["status"]
    period = d["period"]
    cov_rows = {(r["iso_week"], r.get("plant")): r for r in d["coverage"]["rows"]}
    fleet_cov = cov_rows.get((w, "ALL"), {}).get("coverage")
    tk = {r["iso_week"]: r for r in d["tickets"]["rows"]}
    tickets_w, tickets_p = tk.get(w, {}).get("tickets"), tk.get(prev, {}).get("tickets")
    top_driver = iw["cost_by_driver"][0] if iw["cost_by_driver"] else None

    # --- headline sentences (deterministic, from tool outputs)
    lines_txt = []
    if dt.get("it_downtime_share") is not None:
        lines_txt.append(
            f"IT faults caused <strong>{pct(dt['it_downtime_share'])}</strong> of unplanned line-stop time "
            f"({num(dt['it_minutes'])} of {num(dt['downtime_minutes'])} minutes across {num(dt['lines'])} lines)"
            + (f", against {pct(dtp.get('it_downtime_share'))} the week before." if dtp else "."))
    if top_driver:
        lines_txt.append(f"The largest cost driver was <strong>{E(top_driver['driver'])}</strong> "
                         f"({zar(top_driver['cost_low_zar'])}–{zar(top_driver['cost_high_zar'])}).")
    if tickets_w is not None:
        lines_txt.append(f"The service desk logged <strong>{num(tickets_w)}</strong> incidents"
                         + (f" ({signed_pct((tickets_w - tickets_p) / tickets_p)} vs {short_week(prev)})."
                            if isinstance(tickets_w, (int, float)) and isinstance(tickets_p, (int, float)) and tickets_p
                            else "."))
    crit = [x for x in d["dq"]["warnings"] if x["severity"] == "critical"]
    if crit:
        lines_txt.append(f"Read with care: {len(crit)} critical data quality warning"
                         f"{'s' if len(crit) > 1 else ''} this week (see <a href='#trust'>Trust</a>).")

    # --- tiles
    def tile(label, value, sub=""):
        return f'<div class="tile"><div class="label">{E(label)}</div><div class="value">{value}</div>' \
               f'<div class="sub">{sub}</div></div>'
    bsod = {r["iso_week"]: r for r in d["kpi"]["rows"]}
    tiles = "".join([
        tile("IT share of line-stop time", pct(dt.get("it_downtime_share")),
             f"{short_week(prev)}: {pct(dtp.get('it_downtime_share'))}" if dtp else ""),
        tile("IT line-stop minutes", num(dt.get("it_minutes")), f"{num(dt.get('it_stops'))} stops"),
        tile("Incidents logged", num(tickets_w), f"{short_week(prev)}: {num(tickets_p)}" if tickets_p else ""),
        tile("Telemetry coverage", pct(fleet_cov), badge(status)),
        tile("Blue screens / 1,000 device-weeks", num(bsod.get(w, {}).get("value"), 1),
             f"{short_week(prev)}: {num(bsod.get(prev, {}).get('value'), 1)}" if prev else ""),
    ])

    # --- trend charts
    weeks = d["weeks"]
    trend = {}
    for r in d["impact_trend"]["rows"]:
        if r.get("suppressed"):
            continue
        k = r["iso_week"]
        a = trend.setdefault(k, [0.0, 0.0])
        a[0] += r.get("it_app_minutes") or 0
        a[1] += r.get("it_terminal_minutes") or 0
    downtime_chart = columns(weeks, [("MES client faults", [trend.get(x, [0, 0])[0] for x in weeks]),
                                     ("Station PC faults", [trend.get(x, [0, 0])[1] for x in weeks])],
                             highlight=w, unit=" min")
    downtime_table = data_view(["Week", "MES client (min)", "Station PC (min)"],
                               [[x, num(trend.get(x, [0, 0])[0]), num(trend.get(x, [0, 0])[1])] for x in weeks])
    ticket_chart = columns(weeks, [("Incidents", [tk.get(x, {}).get("tickets") for x in weeks])],
                           highlight=w, unit="")
    ticket_table = data_view(["Week", "Incidents", "Per 1,000 devices", "Median resolve (h)"],
                             [[x, num(tk.get(x, {}).get("tickets")), num(tk.get(x, {}).get("tickets_per_1k_devices"), 1),
                               num(tk.get(x, {}).get("mttr_p50_hours"), 1)] for x in weeks])
    plants = [("ROS", "Rosslyn"), ("GQB", "Gqeberha"), ("DBN", "Durban")]
    outage = {}
    for x in d["dq"]["warnings"]:
        if x["code"] == "telemetry_outage":
            outage[x["week"]] = x["message"]
    for r in d["timeline"]["rows"]:
        if r["kind"] == "telemetry_outage":
            outage[r["iso_week"]] = f"{r['plant']}: {r['detail']}"
    cov_chart = lines(weeks, [(name, [cov_rows.get((x, code), {}).get("coverage") for x in weeks])
                              for code, name in plants], ymin=0.0, ymax=1.0, shade=outage)
    cov_table = data_view(["Week", *[n for _, n in plants]],
                          [[x, *[pct(cov_rows.get((x, c), {}).get("coverage")) for c, _ in plants]] for x in weeks])

    # --- cost breakdown
    cost_rows = [[E(COST_TYPE.get(r["cost_type"], r["cost_type"])), E(r["driver"]),
                  f"{num(r['quantity'])} {E(r['quantity_unit'])}",
                  f"{zar(r['cost_low_zar'])} – {zar(r['cost_high_zar'])}"] for r in iw["cost_by_driver"][:8]]

    # --- why: changes
    ch = d["changes"]["rows"]
    strong = [c for c in ch if not c.get("suppressed") and c["evidence"] in ("strong", "moderate")]
    weak = [c for c in ch if not c.get("suppressed") and c["evidence"] == "weak"]
    metric_name = METRIC_NAME
    why_rows = []
    for c in strong:
        detail = f"{metric_name.get(c['top_metric'], c['top_metric'])} {signed_pct(c['top_rel_change'])} " \
                 f"on {num(c['scope_devices'])} targeted devices"
        mech = E(c["mechanism"]) if c.get("mechanism") else "–"
        rivals = E(c["competing_changes"]) if c.get("competing_changes") else "none"
        why_rows.append([f"<strong>{E(c['change_id'])}</strong><br>{E(c['description'])}<br>"
                         f"<span class='meta'>{E(str(c['actual_start'])[:16])}</span>",
                         badge(c["evidence"]), E(detail), mech, rivals])
    weak_items = "".join(
        f"<li><strong>{E(c['change_id'])}</strong> {E(c['description'])}: "
        f"{E(metric_name.get(c['top_metric'], c['top_metric']))} {signed_pct(c['top_rel_change'])}"
        f"{', explained by ' + E(c['mechanism_change']) if c.get('mechanism_change') and c['change_id'] not in c['mechanism_change'] else ''}"
        f"{'' if c['has_comparison'] else ' (no comparison group: targets every device)'}</li>" for c in weak)

    # --- remediation
    rem_rows = []
    for r in d["remediation"]["rows"]:
        effect = "–"
        if r.get("rel_change") is not None:
            what = (f"{r['app'].removesuffix('.exe')} crash rate" if r.get("app")
                    else METRIC_NAME.get(r["metric"], r["metric"] or ""))
            effect = f"{E(what)} {signed_pct(r['rel_change'])} ({E(week_range(r['before_weeks']))} → " \
                     f"{E(week_range(r['after_weeks']))})"
        elif r.get("implementation_evidence"):
            effect = E(r["implementation_evidence"])
        rem_rows.append([f"<strong>{E(r['action_id'])}</strong> {E(r['title'])}", E(r["status"]),
                         E(str(r["actioned_on"] or "–")), badge(r["verdict"]), effect])

    # --- service desk
    cat_rows = [[E(str(r.get("category", ""))), num(r.get("tickets")) if not r.get("suppressed") else E(str(r["tickets"])),
                 num(r.get("mtta_p50_min")), num(r.get("mttr_p50_hours"), 1), pct(r.get("sla_breach_rate")),
                 pct(r.get("reopen_rate"))]
                for r in sorted(d["tickets_by_category"]["rows"],
                                key=lambda x: -(x.get("tickets") if isinstance(x.get("tickets"), (int, float)) else 0))]

    # --- timeline
    kind_label = {"change": "Change", "major_incident": "Major incident", "detection": "Detected",
                  "problem": "Problem", "telemetry_outage": "Outage", "remediation": "Remediation"}
    tl = "".join(f"<li><time>{E(str(r['event_at'])[:16])}</time><span class='kind'>{E(kind_label.get(r['kind'], r['kind']))}</span>"
                 f"<span><strong>{E(r['ref'])}</strong> {E(r['title'])}<br><span class='meta'>{E(r['plant'])} · {E(r['detail'])}</span></span></li>"
                 for r in d["timeline"]["rows"])

    # --- trust
    warn = "".join(f"<li>{badge(x['severity'])} <span class='meta'>{E(x['scope'])}</span> {E(x['message'])}</li>"
                   for x in d["dq"]["warnings"] if x["severity"] in ("critical", "warning"))
    ca = iw["cost_assumptions"]
    margin = ", ".join(f"{p} {zar(v['low'])}–{zar(v['high'])}" for p, v in ca["margin_per_line_minute"].items())

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kestrel fleet report {E(w)}</title>
<style>{CSS}</style></head>
<body><main class="viz-root">
<header>
  <p class="eyebrow">Kestrel Assembly Group · Windows fleet · weekly executive report</p>
  <h1>{E(w)} <span class="meta">{E(nice_date(period['start_date']))} – {E(nice_date(period['end_date']))}</span></h1>
  <p class="disclaimer">Fictional client, synthetic data (Ample Roots Digital case study). Costs are illustrative.
  Evidence and remediation status use data to {E(nice_date(d['data_end']))}.</p>
</header>

<section aria-labelledby="happened">
  <h2 id="happened">What happened and what it cost</h2>
  <div class="hero"><div class="label">Estimated cost of IT-caused disruption this week (illustrative)</div>
    <div class="figure">{zar(cost['low'])} – {zar(cost['high'])}</div></div>
  <ul class="headline">{''.join(f'<li>{x}</li>' for x in lines_txt)}</ul>
  <div class="tiles">{tiles}</div>
  <div class="grid2">
    <figure><figcaption>IT-caused line-stop minutes per week</figcaption>{legend(['MES client faults', 'Station PC faults'])}{downtime_chart}{downtime_table}</figure>
    <figure><figcaption>Service desk incidents per week</figcaption>{ticket_chart}{ticket_table}</figure>
  </div>
  {table(['Cost type', 'Driver', 'Quantity', 'Range (ZAR)'], cost_rows, 'Cost by driver this week')}
</section>

<section aria-labelledby="why">
  <h2 id="why">Why: logged changes before the shifts</h2>
  <p class="note">Each change is compared on the devices it targeted versus the devices it did not, before and after.
  This is <strong>evidence, not proof</strong>: "concentrated in" names the app version or bug check code carrying the
  shift, and other changes in the same window are listed so they can be ruled out.</p>
  {table(['Change', 'Evidence', 'What moved afterwards', 'Concentrated in', 'Other changes in the window'], why_rows) if why_rows else '<p>No change this week or last shows moderate or strong evidence.</p>'}
  {f'<details><summary>Considered, weak evidence ({len(weak)})</summary><ul>{weak_items}</ul></details>' if weak else ''}
  <h3>This week in order</h3>
  <ol class="timeline">{tl or '<li>No dated events.</li>'}</ol>
</section>

<section aria-labelledby="done">
  <h2 id="done">What's being done, and did it work <span class="meta">(status as of {E(nice_date(d['data_end']))})</span></h2>
  <p class="note">The remediation register is a manual claim. Each action is checked against its change record and telemetry.</p>
  {table(['Action', 'Register status', 'Actioned', 'Verdict', 'Evidence'], rem_rows)}
</section>

<section aria-labelledby="desk">
  <h2 id="desk">Service desk this week</h2>
  {table(['Category', 'Incidents', 'Median acknowledge (min)', 'Median resolve (h)', 'Missed SLA', 'Reopened'], cat_rows)}
  <p class="note">Tickets show what people report. Silent failures, such as devices that stop sending telemetry, raise none.
  Resolution figures exclude child tickets of a major incident.</p>
</section>

<section aria-labelledby="trust">
  <h2 id="trust">How far to trust these numbers</h2>
  <figure><figcaption>Telemetry coverage by plant (shaded: outage week)</figcaption>{legend([n for _, n in plants])}{cov_chart}{cov_table}</figure>
  <ul class="warnings">{warn or '<li>No warnings this week.</li>'}</ul>
</section>

<section aria-labelledby="briefing">
  <h2 id="briefing">Analyst briefing</h2>
  {briefing_html or '<p class="note">No approved briefing for this week yet. Drafts are published here only after a named reviewer approves them.</p>'}
</section>

<footer>
  <h2>Assumptions and definitions</h2>
  <ul>
    <li>Costs are <strong>illustrative</strong> ({E(ca['source'])}). Margin per line-minute: {E(margin)}; overtime recovers
        {pct(ca['output_recovered_by_overtime']['high_cost'])}–{pct(ca['output_recovered_by_overtime']['low_cost'])} of lost output.
        Office/engineering time lost per app crash {ca['minutes_lost_per_app_crash']['low']}–{ca['minutes_lost_per_app_crash']['high']} min,
        per blue screen {ca['minutes_lost_per_blue_screen']['low']}–{ca['minutes_lost_per_blue_screen']['high']} min.
        Shop-floor impact is counted once, as line downtime.</li>
    <li>Line stops come from the MES stop log (duplicates removed; IT includes OTHER stops whose comment names a PC, screen, reboot or MES fault).</li>
    <li>Coverage = devices active in Intune that sent any telemetry that week ÷ devices active in Intune.</li>
    <li>Groups smaller than 10 devices or events are suppressed. No device, user or caller is identified anywhere in this report.</li>
  </ul>
  <p class="meta">Generated {generated} from the governed fleet tools.</p>
</footer>
</main></body></html>"""


CSS = """
:root{color-scheme:light;
 --page:#f9f9f7;--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
 --series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;
 --good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--neutral:#898781;--shade:rgba(208,59,59,.08)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
 --page:#0d0d0d;--surface-1:#1a1a19;--text-primary:#f0efec;--text-secondary:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--shade:rgba(208,59,59,.16)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface-1:#1a1a19;--text-primary:#f0efec;
 --text-secondary:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--shade:rgba(208,59,59,.16)}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text-primary);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
header,section,footer{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px}
h1{font-size:28px;margin:4px 0}h2{font-size:19px;margin:0 0 12px}h3{font-size:15px;margin:20px 0 8px}
.eyebrow,.meta,.disclaimer,.note,.tick,figcaption,.label,.sub{color:var(--text-secondary)}
.eyebrow{margin:0;font-size:13px}.meta{font-size:13px;font-weight:400}.disclaimer{margin:0;font-size:13px}
.note{font-size:14px}
.hero .label{font-size:14px}.hero .figure{font-size:48px;font-weight:600;line-height:1.1;margin:4px 0 8px}
.headline{padding-left:18px}.headline li{margin:4px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
.tile{border:1px solid var(--border);border-radius:10px;padding:12px}
.tile .label{font-size:13px}.tile .value{font-size:26px;font-weight:600}.tile .sub{font-size:13px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
figure{margin:12px 0}figcaption{font-size:14px;margin-bottom:6px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .tick{font-size:11px;fill:var(--muted)}
.chart .value{font-size:12px;fill:var(--text-primary);font-weight:600}.chart .shade{fill:var(--shade)}
.chart .dot{stroke:var(--surface-1);stroke-width:2}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--text-secondary);margin-bottom:4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.tablewrap{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
caption{text-align:left;color:var(--text-secondary);font-size:14px;padding-bottom:6px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
th{font-weight:600;color:var(--text-secondary);font-size:13px}
td{font-variant-numeric:tabular-nums}
details{margin:6px 0;font-size:14px}summary{cursor:pointer;color:var(--text-secondary)}
.badge{display:inline-flex;align-items:center;gap:4px;white-space:nowrap;font-size:13px;font-weight:600;
 padding:2px 8px;border-radius:999px;border:1px solid currentColor}
.badge.good{color:var(--good)}.badge.warning{color:#b37800}.badge.serious{color:#c45a2e}
.badge.critical{color:var(--critical)}.badge.neutral{color:var(--neutral)}
:root[data-theme="dark"] .badge.warning{color:var(--warning)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .badge.warning{color:var(--warning)}
 :root:where(:not([data-theme="light"])) .badge.serious{color:var(--serious)}}
.timeline{list-style:none;padding:0;margin:0}
.timeline li{display:grid;grid-template-columns:130px 120px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--grid);font-size:14px}
.timeline time{color:var(--text-secondary);font-variant-numeric:tabular-nums}
.timeline .kind{font-weight:600}
.warnings{padding-left:0;list-style:none}.warnings li{margin:8px 0;font-size:14px}
footer ul{padding-left:18px;font-size:14px}
@media (max-width:640px){.timeline li{grid-template-columns:1fr}.hero .figure{font-size:36px}}
"""


def build(week: str, tools: FleetTools | None = None, out_dir: Path | None = None) -> Path:
    t = tools or FleetTools(caller="report")
    w = t.v.week(week)
    if w is None:
        raise ToolInputError("--week is required, e.g. 2026-W41")
    page = render(gather(t, w), approved_briefing(w))
    t._out.check(page)                     # same fail-closed identifier and name check as every tool
    out = (out_dir or REPORTS) / f"{w}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the weekly executive report.")
    ap.add_argument("--week", required=True, help="ISO week, e.g. 2026-W41")
    args = ap.parse_args(argv)
    try:
        print(f"Wrote {build(args.week)}")
    except ToolInputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
