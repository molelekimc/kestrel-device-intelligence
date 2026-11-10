"""Weekly briefing flow with a scripted stand-in for the Claude API (no network calls)."""

import json
from types import SimpleNamespace as NS

import pytest

from automation import contract, drafts, llm, review, weekly_briefing
from mcp_server.tools import FleetTools

GOOD = """# Kestrel fleet briefing, 2026-W41

## 1. Observed change
- App crash rate for the fleet rose from 274 to 559 per 1,000 device-weeks in 2026-W41
  (Mon 5 Oct to Sun 11 Oct 2026), 13,763 devices reporting.

## 2. Supporting evidence
- Like-for-like (same 12,900 devices in W40 and W41) the rate still doubled.
- KestrelMES.exe 4.2.0 accounts for 4,513 crashes on 3,349 devices, all in W41.

## 3. Data quality warnings
- Durban telemetry outage Mon 5 to Fri 9 Oct; Durban coverage 33%.
- Fleet coverage fell to 76%, and the reporting cohort mix shifted.

## 4. Hypotheses
- Hypothesis: the KestrelMES 4.2.0 release introduced a crash on shop-floor terminals.
- **Hypothesis:** some of the rise reflects which devices reported during the Durban outage.

## 5. Recommended next checks
- Check with the MES owner whether 4.2.0 was released in W41 and to which plants.
- Compare the crash rate like-for-like for W40 to W42 once Durban data is complete.
"""


def block(**kw):
    return NS(**kw)


def response(content, stop="end_turn"):
    return NS(content=content, stop_reason=stop, model="claude-test",
              usage=NS(input_tokens=100, output_tokens=50))


class FakeClient:
    """Plays back scripted responses and records each request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=lambda o: vars(o))))
        return self.script.pop(0)


@pytest.fixture
def tmp_briefings(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts, "BRIEFINGS", tmp_path / "briefings")
    return tmp_path / "briefings"


@pytest.fixture(scope="module")
def tools(mini_db_path):
    return FleetTools(mini_db_path, caller="briefing")


def scripted_good():
    return FakeClient([
        response([block(type="thinking", thinking=""),
                  block(type="tool_use", id="t1", name="get_kpi",
                        input={"metric": "app_crash_rate", "week_from": "2026-W40", "week_to": "2026-W41"}),
                  block(type="tool_use", id="t2", name="get_kpi",
                        input={"metric": "app_crash_rate", "plant": "Atlantis"})], stop="tool_use"),
        response([block(type="text", text=GOOD)]),
    ])


# ----------------------------------------------------------------- contract

def test_good_briefing_meets_contract():
    assert contract.check(GOOD, "2026-W41", {"telemetry_outage", "critical_coverage"}) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda t: t.replace("## 4. Hypotheses", "## 4. Theories"), "missing sections"),
    (lambda t: t.replace("rose from 274", "rose, caused by MES, from 274"), "causal language"),
    (lambda t: t.replace("- Hypothesis: the KestrelMES", "- the KestrelMES"), "hypothesis not labelled"),
    (lambda t: t.replace("- Check with the MES owner", "- Roll back 25H2 and check with the MES owner"),
     "does not start with a check verb"),
    (lambda t: t.replace("- Check with the MES owner whether",
                         "- Check, then reinstall KestrelMES if"), "recommends remediation"),
    (lambda t: t.replace("13,763 devices", "13,763 devices incl. DBN-SF-12345"), "contains a hostname"),
    (lambda t: t.replace("Durban telemetry outage", "Durban telemetry gap"), "telemetry_outage"),
    (lambda t: t.replace("2026-W41", "this week"), "is not named"),
])
def test_contract_violations(mutate, expected):
    v = contract.check(mutate(GOOD), "2026-W41", {"telemetry_outage"})
    assert any(expected in x for x in v), v


# ----------------------------------------------------------------- drafting

def test_draft_runs_governed_tools_and_saves(tools, tmp_briefings):
    client = scripted_good()
    path, violations = weekly_briefing.draft("2026-W41", model="claude-test", client=client, tools=tools)
    assert violations == []
    meta, body, appendix = drafts.load(path)
    assert meta["status"] == "draft" and meta["week"] == "2026-W41"
    assert meta["period"] == "2026-10-05 to 2026-10-11"
    assert [c["ok"] for c in meta["tool_calls"]] == [True, False]   # bad plant rejected by guardrail
    assert body.startswith("# Kestrel fleet briefing") and "## Evidence appendix" in appendix

    # The model got the contract, the governed tool list, and the guardrail's error text.
    first = client.requests[0]
    assert "Recommended next checks" in first["system"]
    assert {t["name"] for t in first["tools"]} >= {"get_kpi", "compare_cohorts", "list_threshold_breaches"}
    results = client.requests[1]["messages"][-1]["content"]
    assert results[1]["is_error"] and "Unknown plant" in results[1]["content"]
    # Thinking blocks are passed back unchanged (append-only history).
    assert client.requests[1]["messages"][1]["content"][0]["type"] == "thinking"


def test_refusal_writes_nothing(tools, tmp_briefings):
    client = FakeClient([response([], stop="refusal")])
    with pytest.raises(llm.BriefingModelError, match="refusal"):
        weekly_briefing.draft("2026-W41", client=client, tools=tools)
    assert not (tmp_briefings / "drafts").exists()


def test_fallbacks_only_for_supported_models(tools, tmp_briefings):
    c1 = scripted_good()
    weekly_briefing.draft("2026-W41", model="claude-opus-5-5", client=c1, tools=tools)
    assert c1.requests[0]["fallbacks"] == "default"
    c2 = scripted_good()
    weekly_briefing.draft("2026-W41", model="claude-haiku-5-5", client=c2, tools=tools)
    assert "fallbacks" not in c2.requests[0]


# ----------------------------------------------------------------- review

def make_draft(tools, text=GOOD):
    client = FakeClient([response([block(type="text", text=text)])])
    return weekly_briefing.draft("2026-W41", client=client, tools=tools)


def test_approve_moves_only_approved(tools, tmp_briefings):
    path, _ = make_draft(tools)
    target = review.approve(drafts.Path(path), "Reviewer A", note="numbers checked")
    assert not drafts.Path(path).exists() and target.parent.name == "approved"
    meta, _, _ = drafts.load(target)
    assert meta["status"] == "approved" and meta["approved_by"] == "Reviewer A"
    assert meta["review_history"][-1]["action"] == "approved"


def test_approve_blocked_by_contract_violations(tools, tmp_briefings):
    path, violations = make_draft(tools, GOOD.replace("Hypothesis: the", "the"))
    assert violations
    with pytest.raises(review.ReviewError, match="does not meet the briefing contract"):
        review.approve(drafts.Path(path), "Reviewer A")
    with pytest.raises(review.ReviewError, match="needs a --note"):
        review.approve(drafts.Path(path), "Reviewer A", accept_violations=True)
    assert drafts.Path(path).exists()


def test_edit_then_approve(tools, tmp_briefings, tmp_path):
    path, violations = make_draft(tools, GOOD.replace("Hypothesis: the", "the"))
    fixed = tmp_path / "fixed.md"
    fixed.write_text(GOOD)
    assert review.edit(drafts.Path(path), "Reviewer B", from_file=fixed) == []
    meta, _, _ = drafts.load(path)
    assert meta["status"] == "edited" and "lines changed" in meta["review_history"][-1]["note"]
    review.approve(drafts.Path(path), "Reviewer B")
    assert list((tmp_briefings / "approved").glob("*.md"))


def test_rejected_stays_in_drafts_and_cannot_be_approved(tools, tmp_briefings):
    path, _ = make_draft(tools)
    with pytest.raises(review.ReviewError, match="reason"):
        review.reject(drafts.Path(path), "Reviewer C", note="")
    review.reject(drafts.Path(path), "Reviewer C", note="overstates the MES link")
    assert drafts.load(path)[0]["status"] == "rejected"
    with pytest.raises(review.ReviewError, match="already rejected"):
        review.approve(drafts.Path(path), "Reviewer C")
    assert not (tmp_briefings / "approved").exists()
