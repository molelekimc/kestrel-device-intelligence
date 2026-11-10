"""Grading logic of the eval harness, with a scripted grader (no network)."""

import json
from types import SimpleNamespace as NS

import pytest

from evals import grader, run
from evals.grader import Criterion, Grade


def q(qid):
    return next(x for x in run.load_questions() if x["id"] == qid)


def test_questions_file_is_well_formed():
    qs = run.load_questions()
    assert 10 <= len(qs) <= 15
    assert len({x["id"] for x in qs}) == len(qs)
    for x in qs:
        assert x["question"].strip() and x["rubric"], x["id"]
        assert x.get("rules", {}).get("all_of") or x.get("rules", {}).get("none_of"), x["id"]
    # Every storyline in the answer key is covered.
    covered = {s for x in qs for s in (x["storyline"] if isinstance(x["storyline"], list) else [x["storyline"]])}
    assert {"A", "B", "C", "D", "E", "tracker", "guardrail"} <= covered


def test_rules_reward_model_and_build_not_plant():
    good = ("The rise is concentrated in HP Elite Mini 800 G9 terminals after the move to Win11 25H2; "
            "Durban's Elite Minis on 25H2 show the same rate, so it is not a plant issue.")
    bad = "Gqeberha has a problem with its shop floor. Gqeberha is the root cause."
    assert all(r["pass"] for r in grader.apply_rules(good, q("A1_gqeberha_bsod")["rules"]))
    assert not all(r["pass"] for r in grader.apply_rules(bad, q("A1_gqeberha_bsod")["rules"]))


def test_rules_catch_leaked_identifiers():
    leak = "The worst device is GQB-SF-12345, I can share more."
    refusal = "I can only see aggregate metrics, not individual devices; here is the breakdown by model."
    assert not all(r["pass"] for r in grader.apply_rules(leak, q("G1_hostnames")["rules"]))
    assert all(r["pass"] for r in grader.apply_rules(refusal, q("G1_hostnames")["rules"]))


def test_rules_require_like_for_like_for_rosslyn():
    naive = "Yes, Rosslyn shop-floor logon really improved from 66s to 54s."
    careful = ("Across all reporting devices logon fell 12s, but like-for-like (same 1,248 devices) it fell "
               "only 2.7s; coverage dropped as LTSC terminals stopped reporting.")
    assert not all(r["pass"] for r in grader.apply_rules(naive, q("B1_rosslyn_logon")["rules"]))
    assert all(r["pass"] for r in grader.apply_rules(careful, q("B1_rosslyn_logon")["rules"]))


class FakeGrader:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []
        self.beta = NS(messages=NS(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        crit = [Criterion(criterion=c, passed=v, reason="scripted")
                for c, v in zip(self.rubric, self.verdicts)]
        return NS(stop_reason="end_turn", parsed_output=Grade(criteria=crit))


def test_llm_grade_maps_verdicts_and_sends_reference():
    rubric = ["criterion one", "criterion two"]
    fake = FakeGrader([True, False])
    fake.rubric = rubric
    out = grader.llm_grade("Q?", "answer text", rubric, ["fact: 41.9 vs 3.0"], model="claude-opus-5-5", client=fake)
    assert [o["pass"] for o in out] == [True, False]
    sent = fake.calls[0]["messages"][0]["content"]
    assert "fact: 41.9 vs 3.0" in sent and "answer text" in sent
    assert fake.calls[0]["fallbacks"] == "default"


def test_grade_answers_rules_only_and_scorecard(tmp_path):
    qs = [q("C1_durban_w41"), q("G1_hostnames")]
    answers = {
        "C1_durban_w41": {"answer": "Durban had a telemetry outage Mon 5 to Fri 9 Oct; W41 numbers are unreliable."},
        "G1_hostnames": {"answer": "Top device: GQB-SF-12345"},
    }
    graded = run.grade_answers(qs, answers, {}, None, use_llm=False)
    assert graded["C1_durban_w41"]["pass"] and graded["C1_durban_w41"]["status"] == "rules only"
    assert not graded["G1_hostnames"]["pass"]
    card = {"run_at": "now", "questions": [{"id": x["id"], "storyline": run._storyline(x)} for x in qs],
            "answer_model": "external", "grader_model": "rules only", "answers": graded,
            "answers_skipped": None, "evidence": {x["id"]: {"checks": [], "pass": True} for x in qs},
            "silver": {"pass": True, "checks": [], "raw": {
                "model_all": {"rate": 0.99}, "model_unknown_in_intune": 1, "extra_devices_not_in_truth": 0,
                "extra_devices_all_retired": True, "migration_missed_after_last_telemetry": 0,
                "migration_observed_recall": {"rate": 1.0},
                "failed_marked_done_caught": {"matched": 1, "of": 1}}}}
    md = run.scorecard_md(card)
    assert "1/2 answers pass" in md and "G1_hostnames** missed" in md
    assert "Pipeline fixes found by evaluation" in md
