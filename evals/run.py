"""Evaluation harness.

    python -m evals.run                      # every tier that can run
    python -m evals.run --tier evidence      # no model calls
    python -m evals.run --answers saved.json # grade answers produced elsewhere (e.g. Claude Desktop)
    python -m evals.run --only A1_gqeberha_bsod,C1_durban_w41

Tiers:
  silver    the Silver/Gold device layers against answer_key/device_truth.csv
  evidence  the governed tools return the facts each storyline needs (no model)
  answers   Claude answers each question through the same tools as Claude Desktop; graded by
            rules and an LLM grader against the rubric (needs ANTHROPIC_API_KEY)

Writes evals/results/scorecard_<timestamp>.md and .json."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from automation import llm
from evals import evidence, grader, validate_silver
from mcp_server.server import INSTRUCTIONS
from mcp_server.tools import FleetTools

HERE = Path(__file__).parent
RESULTS = HERE / "results"
RUBRIC_PASS = 0.75     # share of rubric criteria an answer must meet (rules must all pass)

# Silver checks and their minimum rates.
SILVER_THRESHOLDS = {
    "devices_found": 1.0, "azure_ad_id": 1.0, "plant": 0.99, "device_class": 0.99,
    "model_when_known": 0.99, "ltsc_flag": 0.99, "lab_devices_quarantined_as_orphans": 1.0,
    "migration_observed_precision": 0.99, "claimed_not_observed_precision": 0.9,
    "tracker_wave_agrees_with_truth": 0.99, "ltsc_silent_after_agent_24": 0.95,
}

# Pipeline defects found by evaluation and fixed in the pipeline (never in the eval).
PIPELINE_FIXES = [
    {
        "found_by": "silver validation: claimed_not_observed precision",
        "defect": "Rollout reconciliation compared a device's last telemetry date with the tracker's "
                  "scheduled date. Upgrades land a few days after schedule and only show at the next "
                  "boot, so 222 of 245 'claimed but not observed' devices had in fact migrated after "
                  "their last telemetry in the window (precision 9%).",
        "fix": "pipeline/gold.py now derives the scheduled-to-first-25H2 lag from the data (95th "
               "percentile, 8 days) and only calls a Done claim contradicted when telemetry continues "
               "past schedule + lag on the old build; earlier cases are 'unverifiable'.",
        "result": "claimed_not_observed precision 100% (18/18); 18 of 24 true failed-but-Done devices "
                  "caught, the other 6 correctly labelled unverifiable.",
    },
    {
        "found_by": "end-to-end demo test before Phase 5",
        "defect": "The os_build rejection message echoed the normalised value ('23h2') instead of the "
                  "caller's input.",
        "fix": "mcp_server/guardrails.py echoes the original value.",
        "result": "Regression test added.",
    },
]


def _storyline(q: dict) -> str:
    s = q["storyline"]
    return ", ".join(s) if isinstance(s, list) else str(s)


def load_questions(only: set[str] | None = None) -> list[dict]:
    qs = yaml.safe_load((HERE / "questions.yaml").read_text())["questions"]
    return [q for q in qs if not only or q["id"] in only]


def run_silver() -> dict:
    raw = validate_silver.run()
    checks = []
    for name, minimum in SILVER_THRESHOLDS.items():
        v = raw[name]
        checks.append({"check": name, "rate": v["rate"], "matched": v["matched"], "of": v["of"],
                       "minimum": minimum, "pass": v["rate"] is not None and v["rate"] >= minimum})
    return {"checks": checks, "raw": raw, "pass": all(c["pass"] for c in checks)}


def run_evidence(tools: FleetTools, questions: list[dict]) -> dict:
    out = {}
    for q in questions:
        results = [{"check": name, "pass": bool(ok), "detail": detail}
                   for name, ok, detail in getattr(evidence, q["id"])(tools)]
        out[q["id"]] = {"checks": results, "pass": all(r["pass"] for r in results)}
    return out


def produce_answers(tools: FleetTools, questions: list[dict], model: str | None) -> dict:
    answers = {}
    for q in questions:
        try:
            r = llm.run(INSTRUCTIONS, q["question"], tools=tools, model=model)
            answers[q["id"]] = {"answer": r.text, "model": r.model, "tool_calls": r.tool_calls, "usage": r.usage}
        except llm.BriefingModelError as e:
            answers[q["id"]] = {"answer": "", "error": str(e)}
        print(f"  answered {q['id']}", file=sys.stderr)
    return answers


def grade_answers(questions: list[dict], answers: dict, evidence_out: dict, grader_model: str | None,
                  use_llm: bool) -> dict:
    out = {}
    for q in questions:
        a = answers.get(q["id"], {})
        text = a.get("answer", "") if isinstance(a, dict) else str(a)
        if not text:
            out[q["id"]] = {"status": "no answer", "error": a.get("error") if isinstance(a, dict) else None,
                            "pass": False}
            continue
        rules = grader.apply_rules(text, q.get("rules"))
        reference = [f"{c['check']}: {c['detail']}" for c in evidence_out.get(q["id"], {}).get("checks", [])]
        rubric = grader.llm_grade(q["question"], text, q["rubric"], reference, grader_model) if use_llm else []
        rules_ok = all(r["pass"] for r in rules)
        score = sum(c["pass"] for c in rubric) / len(rubric) if rubric else None
        out[q["id"]] = {
            "status": "graded" if use_llm else "rules only",
            "rules": rules, "rubric": rubric, "rubric_score": score,
            "pass": rules_ok and (score is None or score >= RUBRIC_PASS),
            "tool_calls": [c["tool"] for c in a.get("tool_calls", [])] if isinstance(a, dict) else [],
        }
    return out


def scorecard_md(card: dict) -> str:
    L = [f"# Kestrel fleet evaluation scorecard", "",
         f"Run {card['run_at']} · answer model `{card['answer_model'] or '-'}` · grader `{card['grader_model'] or '-'}`", ""]
    s = card["silver"]
    L += ["## 1. Silver layer vs device_truth.csv", "",
          f"**{'PASS' if s['pass'] else 'FAIL'}** · {sum(c['pass'] for c in s['checks'])}/{len(s['checks'])} checks", "",
          "| Check | Match | Rate | Minimum | Result |", "|---|---|---|---|---|"]
    L += [f"| {c['check']} | {c['matched']:,}/{c['of']:,} | {c['rate']:.2%} | {c['minimum']:.0%} | "
          f"{'pass' if c['pass'] else 'FAIL'} |" for c in s["checks"]]
    raw = s["raw"]
    L += ["", f"- Model match across all devices is {raw['model_all']['rate']:.2%}: "
              f"{raw['model_unknown_in_intune']} devices have no model in the Intune export (blank, `null` or `N/A`), "
              "kept as `Unknown` rather than guessed.",
          f"- {raw['extra_devices_not_in_truth']} Silver devices are not in the truth file; all are retired "
          f"(no Intune check-in for 30+ days): {raw['extra_devices_all_retired']}.",
          f"- Telemetry missed {raw['migration_missed_after_last_telemetry']} true migrations "
          f"(recall {raw['migration_observed_recall']['rate']:.1%}); every one happened after the device's last "
          "telemetry in the window, so no rule could observe it.",
          f"- Failed migrations marked Done: {raw['failed_marked_done_caught']['matched']} of "
          f"{raw['failed_marked_done_caught']['of']} flagged as contradicted; the rest are labelled unverifiable.", ""]

    ev = card["evidence"]
    n_ok = sum(v["pass"] for v in ev.values())
    L += ["## 2. Evidence tier (tools return the facts, no model)", "",
          f"**{n_ok}/{len(ev)} questions** have all supporting facts available through the governed tools.", "",
          "| Question | Storyline | Check | Detail | Result |", "|---|---|---|---|---|"]
    for q in card["questions"]:
        for c in ev[q["id"]]["checks"]:
            L.append(f"| {q['id']} | {q['storyline']} | {c['check']} | {c['detail']} | {'pass' if c['pass'] else 'FAIL'} |")
    L.append("")

    L += ["## 3. Answer tier (Claude through the MCP tools)", ""]
    ans = card["answers"]
    if not ans:
        L += [f"Not run: {card['answers_skipped']}", ""]
    else:
        n_ok = sum(v["pass"] for v in ans.values())
        L += [f"**{n_ok}/{len(ans)} answers pass** (all rules, and at least {RUBRIC_PASS:.0%} of rubric criteria).", "",
              "| Question | Rules | Rubric | Tools used | Result |", "|---|---|---|---|---|"]
        for q in card["questions"]:
            a = ans[q["id"]]
            if a["status"] == "no answer":
                L.append(f"| {q['id']} | - | - | - | FAIL ({a.get('error') or 'no answer'}) |")
                continue
            rules = f"{sum(r['pass'] for r in a['rules'])}/{len(a['rules'])}"
            rub = f"{a['rubric_score']:.0%}" if a["rubric_score"] is not None else "not graded"
            L.append(f"| {q['id']} | {rules} | {rub} | {', '.join(dict.fromkeys(a['tool_calls'])) or '-'} | "
                     f"{'pass' if a['pass'] else 'FAIL'} |")
        L.append("")
        for q in card["questions"]:
            a = ans[q["id"]]
            fails = [r["rule"] for r in a.get("rules", []) if not r["pass"]] + \
                    [f"{c['criterion']} ({c['reason']})" for c in a.get("rubric", []) if not c["pass"]]
            if fails:
                L += [f"**{q['id']}** missed:"] + [f"- {f}" for f in fails] + [""]

    L += ["## 4. Pipeline fixes found by evaluation", "",
          "Evals were not changed to make them pass; defects were fixed in the pipeline.", ""]
    for f in PIPELINE_FIXES:
        L += [f"- **Found by:** {f['found_by']}", f"  - **Defect:** {f['defect']}", f"  - **Fix:** {f['fix']}",
              f"  - **Result:** {f['result']}"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kestrel evaluation harness")
    ap.add_argument("--tier", choices=["all", "silver", "evidence", "answers"], default="all")
    ap.add_argument("--model", help=f"answer model (default {llm.DEFAULT_MODEL})")
    ap.add_argument("--grader-model", default=os.environ.get("KESTREL_GRADER_MODEL"),
                    help="LLM grader model (default: same as the answer model default)")
    ap.add_argument("--answers", type=Path, help="JSON {question_id: answer text} to grade instead of asking Claude")
    ap.add_argument("--only", help="comma-separated question ids")
    args = ap.parse_args(argv)

    questions = load_questions(set(args.only.split(",")) if args.only else None)
    tools = FleetTools(caller="eval")
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    card = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questions": [{"id": q["id"], "storyline": _storyline(q)} for q in questions],
            "answer_model": None, "grader_model": None, "silver": None, "evidence": {}, "answers": {},
            "answers_skipped": None, "pipeline_fixes": PIPELINE_FIXES}

    card["silver"] = run_silver()
    card["evidence"] = run_evidence(tools, questions)
    if args.tier in ("all", "answers"):
        if args.answers:
            raw = json.loads(args.answers.read_text())
            answers = {k: (v if isinstance(v, dict) else {"answer": v}) for k, v in raw.items()}
            card["answer_model"] = f"external ({args.answers.name})"
        elif have_key:
            answers = produce_answers(tools, questions, args.model)
            card["answer_model"] = args.model or llm.DEFAULT_MODEL
        else:
            answers = None
            card["answers_skipped"] = ("no ANTHROPIC_API_KEY set. Set it to have Claude answer the questions, "
                                       "or pass --answers with saved answers to grade them by rules.")
        if answers is not None:
            card["grader_model"] = (args.grader_model or llm.DEFAULT_MODEL) if have_key else "rules only (no API key)"
            card["answers"] = grade_answers(questions, answers, card["evidence"],
                                            args.grader_model, use_llm=have_key)
            card["transcripts"] = answers

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (RESULTS / f"scorecard_{stamp}.json").write_text(json.dumps(card, indent=2, default=str))
    md = scorecard_md(card)
    (RESULTS / f"scorecard_{stamp}.md").write_text(md)
    print(md)
    print(f"Wrote evals/results/scorecard_{stamp}.md")
    ok = card["silver"]["pass"] and all(v["pass"] for v in card["evidence"].values()) and \
        all(v["pass"] for v in card["answers"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
