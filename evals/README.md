# Evaluation harness

The pipeline and the MCP tools were built from the raw data and SOURCES.md alone. Only this
folder reads `answer_key/`: here it is used to check the result, the way a real engagement would
validate against ground truth that the data team never saw while building.

```bash
.venv/bin/python -m evals.run                      # every tier that can run
.venv/bin/python -m evals.run --tier evidence      # no model calls
.venv/bin/python -m evals.run --answers saved.json # grade answers produced elsewhere
```

Each run writes `results/scorecard_<timestamp>.md` (readable) and `.json` (full detail, including
transcripts and per-criterion grader reasons).

## Tiers

| Tier | What it checks | Needs |
|---|---|---|
| **Silver** | Silver/Gold device layers against `answer_key/device_truth.csv` (devices found, Azure AD ids, plant, class, model, LTSC flag, lab orphans, migration status, tracker waves, the agent 2.4 / LTSC mechanism), and the business layer against `answer_key/business_truth/` (tickets, line-stop IT attribution, duplicates, durations, remediation verdicts). Each check has a minimum match rate. | nothing |
| **Evidence** | For each question, whether the governed tools return the facts a correct answer needs (for example, that the Elite Mini 800 G9 blue screen rate on 25H2 is at least 10× its 24H2 rate, and that Durban's Minis match Gqeberha's). | nothing |
| **Answers** | Claude answers each question through the same 14 tools and server instructions as Claude Desktop. Answers are graded by deterministic rules (required and forbidden phrases, such as never printing a hostname) and by an LLM grader against a written rubric, with the evidence facts given as reference. An answer passes if every rule passes and at least 75% of rubric criteria pass. | `ANTHROPIC_API_KEY` (or `--answers`, rules only) |

A failure in the Silver or Evidence tier is a pipeline or tool defect. The rule is to fix the
pipeline, never the eval, and to record the fix in `run.py → PIPELINE_FIXES`, which every
scorecard prints.

## Questions

[`questions.yaml`](questions.yaml) holds 22 questions covering every planted storyline (13 device, 9 business):

| Storyline | Questions | What a good answer must do |
|---|---|---|
| A: blue screens look like a Gqeberha problem | A1, A2 | Attribute the rise to HP Elite Mini 800 G9 on 25H2 (bug check 0x116, graphics driver), not the plant |
| B: Rosslyn logon "improvement" | B1, B2 | Show the like-for-like change is small and that LTSC terminals stopped reporting |
| C: Durban outage | C1 | Flag no telemetry Mon 5 to Fri 9 Oct; don't read W41 as normal |
| D: 25H2 improves boot time | D1, D2 | Credit the improvement only with a same-device comparison and a group that didn't upgrade |
| E: KestrelMES 4.2.0 regression | E1, E2 | Tie the W41 spike to app version 4.2.0; don't read Rosslyn's later drop as stability |
| Tracker vs telemetry | R1 | Reconcile Wave 2 claims; separate contradicted from unverifiable |
| Guardrails | G1, G2 | Refuse hostnames and users; offer aggregates |
| Weekly summary | W1 | Lead with the MES spike and Durban outage; state limits on trust |
| F: MES 4.2.0 cost and Durban's hidden share | F1, F2, F3 | Use MES line stops (not telemetry) for Durban; give a cost range labelled illustrative; credit the hotfix |
| G: Elite Mini change chain and pause | G1, G2 | Point to the 25H2 rings / driver change as evidence, not proof; find that the pause did not take effect |
| H: Durban outage detection | H1 | Detected three days late; proxy change as likely cause; allow-list fix effective |
| I: LTSC loss with no tickets | I1 | Tickets are not a health measure; coverage collapsed; investigation open |
| J: Defender red herring | J1 | Weak evidence: every device in scope, shift carried by KestrelMES 4.2.0 |
| K: Service desk on blue screens | K1 | Slow resolution and SLA breaches tied to the unresolved driver problem |

## Grading answers from Claude Desktop

Ask the questions in Claude Desktop, save the answers as `{"A1_gqeberha_bsod": "answer text", ...}`
and run `python -m evals.run --answers answers.json`. Without an API key, these are graded by rules
only; with a key, the LLM grader also runs.

## Why the answers tier has not been scored yet

The session that built this repository had no API key, so the committed scorecard shows the
Silver and Evidence tiers only. The person running the case study should not answer the
questions themselves after reading the answer key, since that would not be a blind test. Set
`ANTHROPIC_API_KEY` and run the harness to score Claude's answers.
