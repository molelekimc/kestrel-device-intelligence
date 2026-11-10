"""Draft the weekly fleet briefing for human review.

    python -m automation.weekly_briefing --week 2026-W41 [--model claude-opus-5-5] [--dry-run]

1. Runs the data quality status and threshold breach checks for the week.
2. Asks Claude to draft a briefing that follows briefing_contract.md, using only
   the governed, read-only fleet tools.
3. Checks the draft against the contract and saves it to briefings/drafts/ with
   status 'draft'. Nothing is published or acted on until a reviewer approves it
   (python -m automation.review)."""

import argparse
import json
import sys
from datetime import datetime, timezone

from automation import contract, drafts, llm
from mcp_server.guardrails import ToolInputError
from mcp_server.tools import FleetTools

SYSTEM = """You are an analytics engineer at Ample Roots Digital preparing the weekly Windows fleet
reliability briefing for Kestrel Assembly Group's IT leadership. Kestrel is a fictional client and
all data is synthetic.

You can only see governed aggregates through the tools. Verify every number you report with a
tool call. Before reporting a change as real, check whether coverage or the reporting cohort
changed, and use compare_cohorts with like_for_like=true. For anything about the Win11 25H2
rollout, use compare_cohorts with group_by='rollout_cohort' so there is a comparison group. Use
get_top_events to see whether a crash or blue screen change is concentrated in one app, version
or bug check code.

Your final message must be only the briefing in Markdown, following this contract exactly:

{contract}"""

PROMPT = """Draft the briefing for {week} ({dates}).

The scheduled checks already ran. Their results are below; investigate with the tools before
writing. Prioritise changes that are large, affect many devices and survive a like-for-like check.

Week data quality status: {dq_status}
Week warnings (critical and warning):
{warnings}

Threshold breaches for {week} (worse first):
{breaches}
"""


def preflight(tools: FleetTools, week: str) -> dict:
    dq = tools.get_data_quality_status(week)
    breaches = tools.list_threshold_breaches(week)
    warnings = [w for w in dq["warnings"] if w["severity"] in ("critical", "warning")]
    return {
        "period": dq["period"],
        "dq_status": dq["data_quality"],
        "warnings": warnings,
        "critical_codes": sorted({w["code"] for w in warnings if w["severity"] == "critical"}),
        "breaches": breaches["rows"],
        "quarantine": dq["quarantine_summary"],
    }


def _breach_line(b: dict) -> str:
    ch = f"{b['change']:+.0%}" if b["change_type"] == "relative" else f"{b['change']:+.3f}"
    conf = f"; confounders: {', '.join(b['confounding_warnings'])}" if b["confounding_warnings"] else ""
    return (f"- {b['slice']}: {b['metric']} {b['previous_value']} -> {b['value']} ({ch}, {b['direction']}), "
            f"{b['devices_reporting']} devices reporting{conf}")


def appendix(pre: dict) -> str:
    lines = ["## Evidence appendix",
             f"Generated from the warehouse for {pre['period']['label']}. "
             "Use it to check the numbers in the briefing.", "",
             f"Data quality status: **{pre['dq_status']['status']}**", "", "### Warnings"]
    lines += [f"- [{w['severity']}] {w['scope']}: {w['message']}" for w in pre["warnings"]] or ["- None"]
    lines += ["", "### Threshold breaches"]
    lines += [_breach_line(b) for b in pre["breaches"]] or ["- None"]
    return "\n".join(lines)


def draft(week: str, model: str | None = None, client=None, tools: FleetTools | None = None,
          out_dir=None) -> tuple[str, list[str]]:
    tools = tools or FleetTools(caller="briefing")
    week = tools.v.week(week)
    if week is None:
        raise ToolInputError("--week is required, e.g. 2026-W41")
    pre = preflight(tools, week)
    prompt = PROMPT.format(
        week=week, dates=f"{pre['period']['start_date']} to {pre['period']['end_date']}",
        dq_status=json.dumps(pre["dq_status"]),
        warnings="\n".join(f"- [{w['severity']}] {w['code']} ({w['scope']}): {w['message']}"
                           for w in pre["warnings"]) or "- none",
        breaches="\n".join(_breach_line(b) for b in pre["breaches"][:25]) or "- none")
    result = llm.run(SYSTEM.format(contract=contract.contract_text()), prompt,
                     tools=tools, model=model, client=client)
    violations = contract.check(result.text, week, set(pre["critical_codes"]))
    now = datetime.now(timezone.utc)
    meta = {
        "week": week,
        "period": f"{pre['period']['start_date']} to {pre['period']['end_date']}",
        "status": "draft",
        "model": result.model,
        "generated_at": now.isoformat(timespec="seconds"),
        "data_quality_status": pre["dq_status"]["status"],
        "critical_codes": pre["critical_codes"],
        "contract_violations": violations,
        "tool_calls": result.tool_calls,
        "usage": result.usage,
        "review_history": [],
    }
    path = (out_dir or drafts.drafts_dir()) / f"{week}_{now:%Y%m%dT%H%M%SZ}.md"
    drafts.save(path, meta, result.text, appendix(pre))
    return str(path), violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", required=True, help="ISO week, e.g. 2026-W41")
    ap.add_argument("--model", default=None, help=f"Claude model id (default {llm.DEFAULT_MODEL}, or KESTREL_MODEL)")
    ap.add_argument("--dry-run", action="store_true", help="run the checks and print the prompt; no model call")
    args = ap.parse_args(argv)
    try:
        if args.dry_run:
            tools = FleetTools(caller="briefing")
            pre = preflight(tools, tools.v.week(args.week))
            print(appendix(pre))
            return 0
        path, violations = draft(args.week, args.model)
    except (ToolInputError, llm.BriefingModelError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Draft saved: {path}")
    if violations:
        print("Contract violations (the reviewer must resolve or explicitly accept these):")
        for v in violations:
            print(f"  - {v}")
    print("Next: python -m automation.review approve|edit|reject <draft> --reviewer <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
